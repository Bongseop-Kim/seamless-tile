# Seamless Tile Inspector — 로컬 서버 플랜

AI 생성 패턴 타일의 seamless 처리 파이프라인을 단계별로 검증하기 위한 로컬 웹 서버. 실제 픽셀 데이터를 가지고 offset → mask → inpaint → inverse offset → 2×2 검수 흐름을 한 화면에서 확인하고, 마스크 폭과 inpaint 강도 같은 파라미터를 슬라이더로 조정하면서 결과를 즉시 비교할 수 있는 도구.

## 목적

- AI가 생성한 타일 후보 이미지를 업로드해서 seamless 처리 결과를 빠르게 검증
- 각 단계의 중간 산출물을 모두 시각적으로 확인 (offset 결과, 마스크, inpaint 결과, 최종 타일)
- 마스크 폭, inpaint denoising strength, 프롬프트 등을 조정하면서 결과 비교
- 2×2, 3×3 반복 미리보기로 seamless 여부를 즉시 확인
- 파라미터 조합별 결과를 저장해서 나중에 비교

## 비목적

- 프로덕션 배포용 서비스가 아님. 로컬에서 본인이 쓰는 검증 도구.
- 사용자 인증, 다중 사용자 지원, 데이터베이스 X
- 모바일 UI 최적화 X. 데스크톱 브라우저에서만 쓸 것
- 실시간 협업 X

## 사용자 흐름

1. 로컬 서버 실행 (`python server.py`)
2. 브라우저에서 `localhost:8000` 접속
3. 타일 후보 이미지 업로드 (drag-and-drop 또는 파일 선택)
4. 좌측에 원본 + 2×2 반복 미리보기가 즉시 표시 (실패 케이스 확인)
5. 우측 컨트롤 패널에서 파라미터 조정
   - 마스크 폭 (px)
   - inpaint 모델 선택 (gpt-image-2, fal.ai SDXL 등)
   - inpaint 프롬프트
   - denoising strength (모델에서 지원하는 경우)
6. "Run pipeline" 버튼 → 단계별 결과가 순차적으로 화면에 나타남
   - offset 결과
   - 마스크 오버레이
   - inpaint 결과 (마스크 영역 표시)
   - inverse offset 결과 (= 최종 seamless 타일)
   - 2×2 / 3×3 반복 미리보기
7. 결과가 마음에 들지 않으면 파라미터 조정 후 재실행
8. 마음에 드는 결과는 "Save run"으로 저장 (이미지 + 파라미터를 한 폴더에)

## 기술 스택

서버: FastAPI (Python 기반, async I/O 지원, Swagger UI 무료)

이미지 처리:
- numpy, Pillow — offset, 마스크 생성, inverse offset
- requests / httpx — gpt-image-2, fal.ai API 호출

프론트엔드: 단일 HTML + Vanilla JS (필요하면 Alpine.js 정도). 빌드 step 없음.

이미지 저장: 로컬 파일시스템 (`./runs/{timestamp}/`). DB 안 씀.

파라미터 저장: 각 run 폴더 안에 `params.json`.

## 디렉토리 구조

```
seamless-tile-inspector/
├── server.py                  # FastAPI entry point
├── pipeline/
│   ├── __init__.py
│   ├── offset.py              # np.roll 래퍼, inverse offset
│   ├── mask.py                # 십자 마스크 생성
│   ├── inpaint.py             # gpt-image-2 / fal.ai 클라이언트
│   └── repeat.py              # 2x2, 3x3 미리보기 생성
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── runs/                      # 각 실행 결과 저장 (gitignore)
│   └── 20260504_165230/
│       ├── original.png
│       ├── offset.png
│       ├── mask.png
│       ├── inpainted.png
│       ├── final.png
│       ├── 2x2.png
│       └── params.json
├── .env                       # API 키 (gitignore)
├── requirements.txt
└── README.md
```

## API 설계

세 개의 엔드포인트만 있으면 충분.

`POST /api/upload` — 원본 이미지 업로드, 임시 ID 반환
- request: multipart/form-data with image file
- response: `{"image_id": "abc123", "size": [1024, 1024]}`

`POST /api/run` — 파이프라인 실행, 단계별 결과 반환
- request: `{"image_id": "abc123", "mask_width": 60, "model": "gpt-image-2", "prompt": "...", "strength": 0.7}`
- response: `{"run_id": "20260504_165230", "stages": {"offset": "/runs/.../offset.png", "mask": "...", "inpainted": "...", "final": "...", "2x2": "..."}}`

`GET /api/runs` — 저장된 run 목록 조회 (비교용)
- response: `[{"run_id": "...", "params": {...}, "thumbnails": {...}}]`

이 외에 `/runs/*`로 정적 파일(결과 이미지) 서빙.

## UI 와이어프레임

```
┌──────────────────────────────────────────────────────────────┐
│  Seamless Tile Inspector                       [Save run]   │
├─────────────────────────────────┬────────────────────────────┤
│                                 │  Parameters                │
│  [drop image here]              │  ┌────────────────────┐    │
│   or click to upload            │  │ Mask width  [60px] │    │
│                                 │  │ ━━━━━●━━━━━━━━━━━ │    │
│                                 │  └────────────────────┘    │
│                                 │  Model                     │
│                                 │  (•) gpt-image-2           │
│                                 │  ( ) fal.ai SDXL           │
│                                 │  Prompt                    │
│                                 │  ┌────────────────────┐    │
│                                 │  │ keep stripe spacing│    │
│                                 │  │ and weave texture  │    │
│                                 │  └────────────────────┘    │
│                                 │  Strength    [0.7]         │
│                                 │  ━━━━━━━●━━━━━━━━━━        │
│                                 │                            │
│                                 │  [Run pipeline]            │
│                                 │                            │
├─────────────────────────────────┴────────────────────────────┤
│  Stages                                                      │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐      │
│  │origin│ │offset│ │ mask │ │inpain│ │final │ │ 2×2  │      │
│  │      │ │      │ │      │ │      │ │      │ │      │      │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘      │
│                                                              │
│  (각 이미지 클릭 시 큰 사이즈로 모달)                       │
├──────────────────────────────────────────────────────────────┤
│  History                                                     │
│  [run_001] mask=40 strength=0.5     [run_002] mask=60 0.7   │
│  [run_003] mask=80 strength=0.8                             │
└──────────────────────────────────────────────────────────────┘
```

## 구현 단계

### Phase 1 — 코어 파이프라인 (서버 없이)

먼저 `pipeline/` 모듈만 만들어서 CLI로 검증. 로컬 PNG 파일 입력 → 단계별 PNG 출력. 이게 잘 돌면 서버 붙임.

- `offset(image, dx=W//2, dy=H//2)` — `np.roll` 사용
- `make_cross_mask(size, mask_width)` — 가운데 십자 흰색, 나머지 검정
- `inpaint(image, mask, model, prompt, strength)` — 모델별로 분기
  - gpt-image-2: 이미지 + 마스크 + 프롬프트로 edits 호출
  - fal.ai: SDXL inpaint 엔드포인트
- `repeat_grid(tile, n=2)` — np 또는 PIL로 NxN 타일링

검증 기준: 1024×1024 후보 이미지를 넣었을 때 6장의 PNG가 정상적으로 떨어지고, 2×2 결과에서 경계가 보이지 않는지.

### Phase 2 — FastAPI 래핑

CLI 모듈을 그대로 함수 호출하는 thin wrapper. 업로드 받은 이미지를 임시 저장하고, 파이프라인 돌린 다음 정적 경로 반환.

- 이미지 업로드 처리 (`UploadFile`)
- 파이프라인 호출
- 결과 이미지를 `runs/{timestamp}/` 폴더에 저장
- 정적 파일 서빙 (`StaticFiles`)
- CORS 설정 (로컬 전용이라 `*`로 열어도 됨)

### Phase 3 — 프론트엔드

단일 `index.html`. Alpine.js 정도면 충분.

- drag-and-drop 업로드
- 파라미터 컨트롤 (slider, radio, textarea)
- "Run pipeline" 버튼이 `/api/run` POST
- 응답으로 받은 단계별 이미지 URL을 grid에 렌더
- 이미지 클릭 시 lightbox로 확대
- 좌우 화살표로 stage 간 빠른 전환

### Phase 4 — 비교 기능

여러 run을 한 화면에서 비교. 핵심 사용 시나리오는 "마스크 폭 40 vs 60 vs 80px 결과를 동시에 보고 싶다."

- History 목록에서 두 개 이상 선택
- 선택된 run들의 final 또는 2×2 결과를 가로로 나열
- 파라미터 차이를 위에 텍스트로 표시

### Phase 5 (옵션) — 자동 비교 그리드

파라미터 sweep 기능. 마스크 폭을 [40, 60, 80, 100], strength를 [0.5, 0.7, 0.9]로 설정하면 12개 조합을 자동으로 돌리고 그리드로 보여줌. 시간은 좀 걸리지만 한 번에 베스트 파라미터를 찾을 수 있음.

## 환경 설정

```bash
python -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn pillow numpy httpx python-multipart python-dotenv
```

`.env`:
```
OPENAI_API_KEY=sk-...
FAL_KEY=...
```

실행:
```bash
uvicorn server:app --reload --port 8000
```

## 핵심 함수 시그니처 초안

```python
# pipeline/offset.py
def offset(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return np.roll(img, shift=(dy, dx), axis=(0, 1))

def inverse_offset(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return np.roll(img, shift=(-dy, -dx), axis=(0, 1))

# pipeline/mask.py
def make_cross_mask(size: tuple[int, int], width: int) -> np.ndarray:
    h, w = size
    mask = np.zeros((h, w), dtype=np.uint8)
    cx, cy = w // 2, h // 2
    mask[:, cx - width // 2 : cx + width // 2] = 255
    mask[cy - width // 2 : cy + width // 2, :] = 255
    return mask

# pipeline/inpaint.py
async def inpaint(
    img: Image.Image,
    mask: Image.Image,
    model: Literal["gpt-image-2", "fal-sdxl"],
    prompt: str,
    strength: float = 0.7,
) -> Image.Image:
    ...

# pipeline/repeat.py
def repeat_grid(tile: Image.Image, n: int) -> Image.Image:
    w, h = tile.size
    out = Image.new(tile.mode, (w * n, h * n))
    for r in range(n):
        for c in range(n):
            out.paste(tile, (c * w, r * h))
    return out
```

## 운영 노트

타일 사이즈는 1024×1024로 가정. 다른 사이즈도 받게 하되, 정사각형 + 8 배수 정도만 허용.

inpaint 호출은 비용이 들기 때문에 같은 파라미터 조합은 캐싱. `params_hash` 기반으로 `runs/`에서 이미 처리된 결과가 있으면 재사용.

API 호출은 비동기로 처리하되, 단일 사용자 도구라 큐는 안 만들고 그냥 `await`로. 동시 실행 1개로 제한.

이미지 비교 시 zoom + pan 기능이 있으면 좋음. 가운데 십자 영역만 확대해서 보면 seam 여부 판단이 빠름. JS 라이브러리 (panzoom) 하나 추가.

## 평가 기준 (이 도구를 만들고 나면 답해야 할 질문)

이 도구를 만드는 진짜 목적은 패턴 타일에서 offset+inpaint 방식이 실제로 작동하는지 확인하는 것. 만든 다음 다음을 평가:

1. 복합 패턴(스트라이프 + 트윌 조직 + 도트 등)에서 inpaint가 십자 영역의 패턴 위상을 정확히 맞춰주는가? (정성 평가)
2. 마스크 폭 sweet spot은? (1024px 기준)
3. gpt-image-2 vs fal.ai SDXL 중 어느 쪽이 텍스처 보존이 나은가?
4. 회전된 패턴(대각선 사선 줄)에서 offset이 위상 정렬 문제를 일으키는가? 일으킨다면 보정 방법은?

## 다음 액션

1. Phase 1 — `pipeline/` 모듈 + CLI로 1024 이미지 한 장으로 end-to-end 검증 (1~2일)
2. 결과가 만족스러우면 Phase 2~3 진행 (반나절~1일)
3. 만족스럽지 않으면 — inpaint 모델 자체 문제인지, offset/mask 처리 문제인지 분리해서 디버그
