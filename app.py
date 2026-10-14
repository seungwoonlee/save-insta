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
DOWNLOAD_DIR = Path(__file__).parent / "downloads"
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
    """Post → UI 용 dict. 슬라이드(여러 장) 게시물은 모든 이미지를 images 에 담는다."""
    if post.typename == "GraphSidecar":
        images = [n.display_url for n in post.get_sidecar_nodes()]
    else:
        images = [post.url]
    return {
        "shortcode": post.shortcode,
        "date": post.date_utc.strftime("%Y-%m-%d %H:%M"),
        "caption": (post.caption or "")[:120],
        "is_video": post.is_video,
        "images": images,
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
    """선택한 이미지를 downloads/<username>/ 폴더에 개별 파일로 저장. body: {username, items:[{name,url}]}"""
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify(error="선택된 사진이 없습니다."), 400
    try:
        username = parse_username(data.get("username", ""))
        urls = [_check_cdn(it["url"]) for it in items]
    except (ValueError, KeyError) as e:
        return jsonify(error=str(e)), 400
    folder = DOWNLOAD_DIR / username
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    try:
        for it, url in zip(items, urls):
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            name = re.sub(r"[^\w.\-]", "_", Path(it["name"]).name).lstrip(".") or "image.jpg"
            (folder / name).write_bytes(r.content)
            saved.append(name)
    except requests.RequestException as e:
        return jsonify(error=f"다운로드 실패: {e} ({len(saved)}장 저장됨)"), 502
    return jsonify(count=len(saved), folder=str(folder))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
