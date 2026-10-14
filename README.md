# save-insta

인스타그램 프로필 URL을 입력하면 게시물 사진 목록(썸네일 그리드)을 보여주고, 선택한 사진을 ZIP으로 다운로드하는 로컬 유틸리티.

## 실행

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py        # http://127.0.0.1:5000
```

## 사용법
1. 프로필 URL(`https://www.instagram.com/<username>/`) 입력 → **사진 불러오기**
2. 사진 클릭(체크)으로 선택, **더 보기**로 24개씩 추가 로드 (여러 장 슬라이드 게시물은 장별로 표시)
3. **선택 다운로드** → ZIP 저장

## 로그인이 필요한 경우
인스타그램은 비로그인 요청을 자주 차단합니다(401/429). 이때는 instaloader 세션을 만들어 사용하세요.

```bash
instaloader --login <내_아이디>          # 세션 파일 생성
INSTA_SESSION_USER=<내_아이디> python app.py
```

## 참고
- 비공개 계정은 지원하지 않습니다(팔로우 중인 로그인 세션 제외). 영상 게시물은 썸네일만 저장됩니다.
- 본인 계정 또는 저작권자의 허락을 받은 콘텐츠에만 사용하세요. 인스타그램 이용약관을 확인하세요.
