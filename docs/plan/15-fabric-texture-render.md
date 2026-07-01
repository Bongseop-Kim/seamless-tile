# 세션 15 — 결정론적 원단 텍스처 렌더 (finalize) [파생출력]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.
> **세션(16~)과 독립** — 기존 stateless 경로 위에서 단독 완결된다. 세션 P0의 실사화 버튼(§8.4)이
> 이 세션의 `/finalize`로 핸드오프하므로 **16보다 먼저** 착수하는 것을 권장.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/photoreal-fabric-render.md` — 전체(§2 접근 선택 B, §5 컴포넌트, §5.6 영역별 질감),
  결정 **R1~R10**. 방향: **생성형 모델 미사용, 결정론 래스터 합성**.
- **현재 코드**: `app/render/raster.py`(SVG→PNG + Pillow 재사용), `app/render/sanitize.py`(allowlist —
  `<filter>` 설계상 거부), `app/validate/seamless.py`(edge_seam 가드), `app/storage/preview.py`(Supabase 업로드),
  `app/api/routes/export.py`·`generate.py`(라우트 패턴), `app/engine/composition.py`(compose·colorway), `app/core/config.py`.
- **선행 세션**: 없음(엔진 MVP·seamless·storage가 이미 있으면 착수 가능).

## 목표
승인된 seamless SVG를 **민짜 rasterize → Pillow tileable 위브 multiply/overlay + normal-map relight**로 합성해
"천 느낌" PNG를 만든다. 결과는 preview와 동급의 **결정론 파생물**이고 **seamless를 유지**한다. 배경/스트라이프/
모티프마다 **영역별 질감**(§5.6)을 지정할 수 있다. 외부 API·모델 호출 0.

## 범위
- `app/render/fabric.py`(신규): 민짜 PNG + bundled tileable 위브/normal 맵을 **wrap 샘플링**으로 합성, 고정 광원 relight.
- 원단 노브(R8): `fabric`(cotton/linen/silk/knit/denim)·`finish`(matte/sheen)·`lighting` → 자산/파라미터 선택. **초기 1~2종**.
- **영역별 질감(R10, §5.6)**: `compose(intent, palette, label_colorway)`를 한 번 더 렌더해 **region 세그맵** 생성 →
  material map(region→fabric)대로 마스크 합성. region 단위 = **color slot 기준**(초기). 미지정 region은 기본 질감 폴백.
- 번들 자산: `app/render/assets/fabric/`에 tileable 위브(+옵션 normal) — **결정론 입력의 일부, 버전 pin**.
- `POST /api/v1/finalize`(명칭 §9): 승인 후보 참조(R9) + 노브 → 텍스처 PNG URL. `X-Request-ID` 전파, 에러 매핑 준수.
- 저장(R7): `app/storage/`로 업로드 → URL, 미설정 시 URL null + warning. best-effort 로그(어떤 fabric/finish/lighting).

## 비범위
- 생성형 이미지 repaint(A안/Gemini) — **채택 안 함**. SVG 필터 벡터 경로(§8 대안).
- element-id 기반 label(밴드/인스턴스별 상이 질감), region별 텍스처 회전(실 방향 맞춤) — 초기 축정렬만(§9).
- 세션 연계 로직 자체 — 여기선 `/finalize` 엔드포인트만. 세션 finalize 노드는 세션 16이 이 로직을 호출.

## 작업 / 만들·수정 파일
- `app/render/fabric.py`(신규 — 합성 코어), `app/render/assets/fabric/*`(번들 자산 + 버전 태그).
- `app/api/routes/finalize.py`(신규 라우트) + `app/api/routes/__init__.py` 등록.
- `app/engine/composition.py`(label colorway 렌더 진입점 재사용 — 신규 엔진 코드 0 목표).
- (해결 필요) 승인 SVG/intent 조회 경로 확정(R9): 세션 state / `(request_id, candidate_id)` 로그 / 클라이언트 전달.

## 수용 기준 (검증 가능)
1. **텍스처 변환**: 승인 seamless SVG → 원단 질감 PNG URL, 육안상 천 느낌.
2. **결정론(핵심)**: 동일 `(SVG, 자산, 파라미터, 렌더러/Pillow 버전)` → **동일 PNG** — 테스트로 봉인.
3. **seamless 유지**: 결과가 `validate/seamless.py` edge_seam 가드 통과(텍스처 grain 감안 소폭 tolerance). **영역별 질감 적용 후에도** 통과.
4. **영역별 질감**: material map으로 배경/스트라이프/모티프에 서로 다른 텍스처 지정 시 각 영역이 배정 텍스처로 렌더, 경계 어긋남 없음. **material map 비면 §5.3 균일 결과와 동일(폴백)** — 테스트로 봉인.
5. **보안 경계 무변경**: `sanitize.py` allowlist 미변경, 필터 없는 SVG만 rasterize — 회귀 테스트.
6. **무외부호출**: finalize가 외부 생성 API를 호출하지 않음 — 테스트로 확인.
7. **degrade**: Storage 미설정 시 URL null + warning, 렌더러 미설치 시 명확한 502.

## 리스크
- 텍스처 자산/파라미터가 결정론 입력의 일부 → **버전 pin 필수**.
- relight 광원·강도 과하면 seamless edge 미세 흔들림 → edge_seam 가드로 탐지, 임계값 실측 보정.
- R9(승인 SVG 조회 경로)·입력 형태(단일 타일 vs N×N center-crop)는 실측 후 확정(§9).

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + 작성/검토 분리 verifier. finalize는 로컬·무료(외부 호출 없음).
