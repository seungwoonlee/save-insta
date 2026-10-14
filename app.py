"""save-insta: 인스타그램 프로필 URL → 게시물 사진 목록 → 선택 다운로드.

실행:  python app.py   →  http://127.0.0.1:5000
공개 계정만 대상으로 하며, 로그인이 필요한 경우 README 의 세션 파일 방법을 사용한다.
"""
from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from urllib.parse import urlparse

import instaloader
import requests
from flask import Flask, Response, jsonify, render_template, request

PAGE_SIZE = 24
BASE_DIR = Path(os.environ.get("SAVE_INSTA_DIR") or Path.cwd() / "downloads")  # 기본: 현재 디렉토리/downloads
ALLOWED_CDN = re.compile(r"(^|\.)(cdninstagram\.com|fbcdn\.net)$")
RESERVED = {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "direct"}

app = Flask(__name__)
_lock = threading.Lock()
_loader: instaloader.Instaloader | None = None
_iterators: dict[str, "Iterator"] = {}  # username -> posts iterator (더 보기용)


def get_loader() -> instaloader.Instaloader:
    global _loader
    if _loader is None:
        _loader = instaloader.Instaloader(
            quiet=True, download_videos=False, save_metadata=False, compress_json=False
        )
        user = os.environ.get("INSTA_SESSION_USER")
        if user:
            _loader.load_session_from_file(user, os.environ.get("INSTA_SESSION_FILE"))
    return _loader


def parse_username(value: str) -> str:
    """프로필 URL 또는 @username 에서 사용자명을 뽑는다."""
    value = value.strip()
    if not value:
        raise ValueError("URL 을 입력하세요.")
    if "instagram.com" in value:
        if not value.startswith(("http://", "https://")):
            value = "https://" + value
        host = urlparse(value).netloc.lower()
        if not re.search(r"(^|\.)instagram\.com$", host):
            raise ValueError("instagram.com 주소가 아닙니다.")
        parts = [p for p in urlparse(value).path.split("/") if p]
        if not parts or parts[0] in RESERVED:
            raise ValueError("프로필 URL 이 아닙니다. (예: https://www.instagram.com/instagram/)")
        value = parts[0]
    value = value.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", value):
        raise ValueError("올바른 사용자명이 아닙니다.")
    return value


def post_to_dict(post: instaloader.Post) -> dict:
    """Post → UI 용 dict. 슬라이드 게시물은 장별로 media 에 담는다. (thumb=미리보기, url=저장할 원본)"""
    if post.typename == "GraphSidecar":
        media = [
            {"thumb": n.display_url, "is_video": n.is_video,
             "url": n.video_url if n.is_video else n.display_url}
            for n in post.get_sidecar_nodes()
        ]
    else:
        media = [{"thumb": post.url, "is_video": post.is_video,
                  "url": post.video_url if post.is_video else post.url}]
    return {
        "shortcode": post.shortcode,
        "folder": f"{post.date_utc:%Y%m%d}_{post.shortcode}",  # 포스트 폴더명
        "date": post.date_utc.strftime("%Y-%m-%d %H:%M"),
        "caption": (post.caption or "")[:120],
        "media": media,
    }


@app.get("/")
def index():
    return render_template("index.html", page_size=PAGE_SIZE)


@app.post("/api/posts")
def api_posts():
    data = request.get_json(silent=True) or {}
    try:
        username = parse_username(data.get("url", ""))
    except ValueError as e:
        return jsonify(error=str(e)), 400

    with _lock:
        try:
            if not data.get("more") or username not in _iterators:
                profile = instaloader.Profile.from_username(get_loader().context, username)
                if profile.is_private and not profile.followed_by_viewer:
                    return jsonify(error="비공개 계정입니다."), 403
                _iterators[username] = iter(profile.get_posts())
            posts = [post_to_dict(p) for p in islice(_iterators[username], PAGE_SIZE)]
        except instaloader.ProfileNotExistsException:
            return jsonify(error="존재하지 않는 계정입니다."), 404
        except instaloader.LoginRequiredException:
            return jsonify(error="로그인이 필요합니다. README 의 세션 설정을 참고하세요."), 401
        except instaloader.TooManyRequestsException:
            return jsonify(error="요청이 너무 많습니다. 잠시 후 다시 시도하세요."), 429
        except instaloader.InstaloaderException as e:
            return jsonify(error=f"인스타그램 오류: {e}"), 502
    return jsonify(username=username, posts=posts, has_more=len(posts) == PAGE_SIZE)


def _safe_name(value: str) -> str:
    name = re.sub(r"[^\w.\-]", "_", Path(str(value)).name).lstrip(".")
    if not name:
        raise ValueError("올바르지 않은 파일/폴더명입니다.")
    return name


def _check_cdn(url: str) -> str:
    p = urlparse(url)
    if p.scheme != "https" or not ALLOWED_CDN.search(p.hostname or ""):
        raise ValueError("허용되지 않은 이미지 주소입니다.")
    return url


@app.get("/api/img")
def api_img():
    """CDN 이미지 프록시 (브라우저 직접 로딩 시 referrer 차단 회피). CDN 도메인만 허용."""
    try:
        url = _check_cdn(request.args.get("u", ""))
    except ValueError as e:
        return str(e), 400
    r = requests.get(url, timeout=20)
    return Response(r.content, status=r.status_code,
                    content_type=r.headers.get("Content-Type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=3600"})


@app.post("/api/download")
def api_download():
    """선택한 사진/영상을 <BASE_DIR>/<계정명>/<포스트폴더>/<번호>.jpg|mp4 로 저장.
    body: {username, items:[{post, name, url}]}"""
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify(error="선택된 사진이 없습니다."), 400
    try:
        username = parse_username(data.get("username", ""))
        urls = [_check_cdn(it["url"]) for it in items]
        posts = [_safe_name(it["post"]) for it in items]
        names = [_safe_name(it["name"]) for it in items]
    except (ValueError, KeyError) as e:
        return jsonify(error=str(e)), 400
    saved = 0
    try:
        for url, post, name in zip(urls, posts, names):
            folder = BASE_DIR / username / post
            folder.mkdir(parents=True, exist_ok=True)
            with requests.get(url, timeout=30, stream=True) as r:
                r.raise_for_status()
                with open(folder / name, "wb") as f:  # 영상은 크므로 스트리밍 저장
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
            saved += 1
    except requests.RequestException as e:
        return jsonify(error=f"다운로드 실패: {e} ({saved}장 저장됨)"), 502
    return jsonify(count=saved, folder=str(BASE_DIR / username))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
