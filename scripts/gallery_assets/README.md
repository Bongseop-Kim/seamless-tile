# gallery_assets

`scripts/gallery.py`가 쓰는 더미 모티프 SVG를 여기에 넣는다.

- 이름 정렬 첫 번째 `*.svg` 한 개를 in-memory 등록(DB 미접근)해 motif 배치 샘플에 쓴다.
- 모티프 '모양'은 갤러리의 변주 대상이 아니다 — 배치/사이즈/배경 조합을 보여주기 위한 자리표시자.
- 비어 있으면 motif 샘플은 스킵되고 배경/stripe 샘플만 생성된다.
- 단색(`currentColor`/단일 fill)이면 단색 경로, 여러 색이면 다색 슬롯 경로로 자동 처리.

예: `<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#000"/></svg>`
