# 세션 8 — 보안 하드닝 + Recraft motif 파이프라인

> 출력/입력 보안을 완성하고, 복잡 도형을 authoring-time에 생성·정규화·캐시한다.

## 선행 조건
세션 3 (registry·composition 인코딩 베이스라인), 세션 4 (generate 출력 경로), 세션 6
(generate·export 라우트·업로드 연결 지점), 세션 7 (이미지 업로드 경로).

## 범위 (in-scope)
- **`render/sanitize.py`** — 출력 인코딩 완성(속성 quote-escape·텍스트 이스케이프) + 태그/속성
  **allowlist**. `href`는 내부 `#id` fragment만(외부 URL·`javascript:` 금지). `motif_id`는
  registry 키 allowlist. hex는 `^#[0-9a-fA-F]{3,8}$`.
- **reference_image 업로드 검증** — multipart 업로드 파일의 포맷·크기·픽셀 상한, 디코드
  타임아웃, 메타데이터 strip. 원격 URL fetch 경로가 아니므로 네트워크 fetch 방어는 이 항목의
  위협 모델이 아니다.
- **`app/adapters/recraft.py` + `motifs/registry.py` 인입** — Recraft 생성 파이프라인:
  - **authoring-time 생성** → 콘텐츠 해시 `motif_id` → registry 등록(런타임은 id만 참조).
  - **intake 정규화**: mm 좌표계, tight bbox·anchor, 단일 `<symbol>` 래핑, filter/embedded
    raster/외부 href 제거 또는 거부, 색을 팔레트 슬롯 참조로 치환.

## 비범위 (out-of-scope)
- 신규 패턴 기능(없음). 기존 산출물의 보안·motif 소스만.

## 작업
- [ ] `sanitize`: 출력 인코딩 + 태그/속성 allowlist + href/motif_id/hex 게이트.
- [ ] composition/generate 출력 경로에 sanitize 연결.
- [ ] reference_image 업로드 검증.
- [ ] Recraft 어댑터(authoring-time) + 콘텐츠 해시 캐시.
- [ ] intake 정규화 파이프라인.
- [ ] 테스트(injection·업로드·intake).

## 만들/수정 파일
`render/sanitize.py`(신규), `app/adapters/recraft.py`(신규), `motifs/registry.py`(수정),
`api/routes/generate.py`(업로드 검증 연결), `tests/test_sanitize.py`·
`tests/test_recraft_intake.py`(신규).

## 수용 기준
- SVG injection 시도가 차단된다: `</svg><script>`, `href="javascript:"`, 외부 `<image href=http..>`,
  비허용 태그/속성(테스트로 각각 차단 확인).
- 악성/과대 업로드(디코드 폭탄·포맷 spoof/polyglot·과대 픽셀·위험 메타데이터)가 거부되거나
  정규화된다.
- Recraft 모킹 SVG가 정규화·해시 등록되고, **같은 입력 → 같은 `motif_id`(캐시 히트)**, 런타임
  결정론 유지.
- intake가 filter/raster/외부 href를 제거하거나 거부하고 색을 슬롯 참조로 치환한다.
- `pytest` 그린.

## 리스크
- sanitize allowlist가 정당한 SVG 기능(예: `patternTransform`·`<use>` transform)을 막지 않도록
  primitive/composition이 쓰는 속성 집합과 정합.
- Recraft 콘텐츠 해시는 정규화 **후** 산출물 기준으로 잡아 동일 형상의 캐시 히트를 보장.
