#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["flask>=3.0", "instaloader>=4.14", "requests>=2.31"]
# ///
"""save-insta: 인스타그램 프로필 URL → 게시물 사진/영상 목록 → 선택 다운로드.

단일 파일 실행 (의존성 자동 설치):   uv run save_insta.py
또는:  pip install flask instaloader requests && python save_insta.py
→ 브라우저가 열리며 http://127.0.0.1:5000 에서 사용. 저장: ./downloads/<계정>/<날짜_포스트코드>/
"""
from __future__ import annotations

import os
import re
import threading
import webbrowser
from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from urllib.parse import urlparse

import instaloader
import requests
from flask import Flask, Response, jsonify, request

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


INDEX_HTML = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Save Insta</title>
<style>
  body{font-family:system-ui,sans-serif;margin:0;background:#fafafa;color:#222}
  header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:12px 16px;z-index:5}
  form{display:flex;gap:8px;flex-wrap:wrap}
  input[type=text]{flex:1;min-width:240px;padding:8px 10px;font-size:15px}
  button{padding:8px 14px;font-size:14px;cursor:pointer}
  #bar{margin-top:8px;display:flex;gap:8px;align-items:center;font-size:14px}
  #msg{padding:12px 16px;color:#c00}
  #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px;padding:16px}
  .cell{position:relative;aspect-ratio:1;background:#ddd;cursor:pointer;overflow:hidden;outline:3px solid transparent}
  .cell.sel{outline-color:#0095f6}
  .cell img{width:100%;height:100%;object-fit:cover}
  .cell input{position:absolute;top:6px;left:6px;width:18px;height:18px}
  .badge{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.6);color:#fff;font-size:12px;padding:2px 6px;border-radius:4px}
  #more{display:block;margin:0 auto 24px}
</style>
</head>
<body>
<header>
  <form id="f">
    <input type="text" id="url" placeholder="https://www.instagram.com/username/" required>
    <button>사진 불러오기</button>
  </form>
  <div id="bar" hidden>
    <button id="all" type="button">전체 선택/해제</button>
    <button id="dl" type="button">선택 다운로드 (<span id="cnt">0</span>)</button>
  </div>
</header>
<div id="msg"></div>
<div id="grid"></div>
<button id="more" hidden>더 보기</button>
<script>
const $ = s => document.querySelector(s);
const sel = new Map();   // key -> {name,url}
let username = "";

const show = m => $("#msg").textContent = m || "";
const count = () => $("#cnt").textContent = sel.size;
const proxy = u => "/api/img?u=" + encodeURIComponent(u);

function render(posts) {
  for (const p of posts) {
    p.media.forEach((m, i) => {
      const key = `${p.shortcode}_${i + 1}`;
      const c = document.createElement("div");
      c.className = "cell";
      c.title = `${p.date}\n${p.caption}`;
      c.innerHTML = `<input type="checkbox"><img loading="lazy" src="${proxy(m.thumb)}">` +
        (m.is_video ? '<span class="badge" style="left:6px;right:auto">▶ 영상</span>' : "") +
        (p.media.length > 1 ? `<span class="badge">${i + 1}/${p.media.length}</span>` : "");
      c.dataset.key = key;
      c.onclick = e => {
        const cb = c.querySelector("input");
        if (e.target !== cb) cb.checked = !cb.checked;
        c.classList.toggle("sel", cb.checked);
        cb.checked ? sel.set(key, {post: p.folder, name: `${i + 1}.${m.is_video ? 'mp4' : 'jpg'}`, url: m.url}) : sel.delete(key);
        count();
      };
      $("#grid").append(c);
    });
  }
}

async function load(more) {
  show("불러오는 중...");
  const r = await fetch("/api/posts", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url: $("#url").value, more})});
  const d = await r.json();
  if (!r.ok) return show(d.error);
  show("");
  username = d.username;
  if (!more) { $("#grid").innerHTML = ""; sel.clear(); count(); }
  render(d.posts);
  $("#bar").hidden = false;
  $("#more").hidden = !d.has_more;
}

$("#f").onsubmit = e => { e.preventDefault(); load(false); };
$("#more").onclick = () => load(true);
$("#all").onclick = () => {
  const cells = [...document.querySelectorAll(".cell")];
  const all = cells.every(c => c.classList.contains("sel"));
  cells.forEach(c => { if (c.classList.contains("sel") === all) c.click(); });
};
$("#dl").onclick = async () => {
  if (!sel.size) return show("선택된 사진이 없습니다.");
  show("저장 중...");
  const r = await fetch("/api/download", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username, items: [...sel.values()]})});
  const d = await r.json();
  if (!r.ok) return show(d.error);
  show("");
  alert(`${d.count}장 저장 완료\n${d.folder}`);
};
</script>
</body>
</html>
'''


@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


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


def main() -> None:
    port = int(os.environ.get("PORT", 5000))
    url = f"http://127.0.0.1:{port}"
    if not os.environ.get("SAVE_INSTA_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"save-insta 실행 중: {url}  (종료: Ctrl+C)")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
