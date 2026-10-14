# save-insta

인스타그램 프로필 URL을 입력하면 게시물 사진 목록(썸네일 그리드)을 보여주고, 선택한 사진을 폴더에 저장하는 로컬 유틸리티.

## 실행 (파일 하나 `save_insta.py`)

```bash
uv run save_insta.py        # 의존성 자동 설치 (uv 필요), 브라우저 자동 실행
# 또는
pip install -r requirements.txt && python save_insta.py
```
`http://127.0.0.1:5000` (`PORT` 환경변수로 변경). 배포 시에는 `save_insta.py` 한 파일만 전달하면 됩니다.

Python 없이 실행 파일로 배포하려면: `pip install pyinstaller && pyinstaller --onefile save_insta.py` → `dist/save_insta`

## 사용법
1. 프로필 URL(`https://www.instagram.com/<username>/`) 입력 → **사진 불러오기**
2. 사진 클릭(체크)으로 선택, **더 보기**로 24개씩 추가 로드 (여러 장 슬라이드 게시물은 장별로 표시)
3. **선택 다운로드** → 기본 `./downloads/<username>/<날짜_포스트코드>/1.jpg, 2.mp4…` 로 저장 (`SAVE_INSTA_DIR` 환경변수로 위치 변경 가능)

## 로그인이 필요한 경우
인스타그램은 비로그인 요청을 자주 차단합니다(401/429). 이때는 instaloader 세션을 만들어 사용하세요.

```bash
instaloader --login <내_아이디>          # 세션 파일 생성
INSTA_SESSION_USER=<내_아이디> python app.py
```

## 참고
- 비공개 계정은 지원하지 않습니다(팔로우 중인 로그인 세션 제외). 영상도 mp4 로 저장됩니다.
- 본인 계정 또는 저작권자의 허락을 받은 콘텐츠에만 사용하세요. 인스타그램 이용약관을 확인하세요.
