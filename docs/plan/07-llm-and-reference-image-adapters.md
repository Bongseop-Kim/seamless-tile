# 세션 7 — LLM & Reference Image 어댑터

> `prompt -> intent`(LLM), `image -> intent`(VLM·색 추출)를 코어 밖 어댑터로 만든다.

## 선행 조건
세션 6 (API 표면).

## 범위 (in-scope)
- **`app/adapters/llm.py` (IntentBuilder)** — `prompt`(+옵션) → intent JSON, `intent_version`
  부여. 검증 실패 시 **제약 re-prompt 1회**(authoring 경계 — 결정론 재현 밖). raw SVG/좌표는
  만들지 않는다.
- **`app/adapters/image.py`** — 참조 이미지 해석:
  - VLM 구조 추출(스타일·모티프·색)을 intent 요소로.
  - 팔레트: K-means/median-cut 8~16색 → 색 슬롯 매핑.
  - 벡터화 적합/부적합 판정을 **구체 규칙**으로: 벡터화 결과 path 수 ≤ N(예: 1500) 그리고 색 수
    ≤ M(예: 32)이면 적합(motif 등록), 초과면 부적합 → 하이브리드 raster 폴백 또는 거부.
    `source_fidelity` 메타로 표기.
- **결정론 경계** — LLM·이미지 단계는 비결정. 산출 intent를 고정·캐시한 뒤 파이프라인에 넣어
  이후 결정론을 보존한다.
- `api/routes/generate.py`에 prompt/image 경로 연결.

## 비범위 (out-of-scope)
- 업로드 보안 검증 세부(세션 8과 공유 — 여기선 인터페이스만).
- Recraft 복잡 도형 생성(세션 8).

## 작업
- [ ] LLM 어댑터 + intent_version + 제약 re-prompt 1회.
- [ ] 이미지 어댑터: VLM 추출 + 팔레트 추출 + 슬롯 매핑.
- [ ] 벡터화 적합/부적합 판정 + 폴백 + source_fidelity.
- [ ] intent 고정·캐시(결정론 경계).
- [ ] 라우트 연결.
- [ ] 테스트(외부 API 모킹).

## 만들/수정 파일
`app/adapters/llm.py`(신규), `app/adapters/image.py`(신규), `api/routes/generate.py`(수정),
`tests/test_adapters.py`(신규).

## 수용 기준
- 모킹된 LLM으로 `prompt -> 유효 intent -> SVG`(검증 통과·결정론 파이프라인).
- 참조 이미지에서 팔레트를 추출해 슬롯에 매핑하고, 부적합 텍스처는 폴백/거부 + `source_fidelity`
  표기.
- 검증 실패 시 re-prompt 1회 후에도 실패하면 `422`.
- `pytest` 그린(외부 API 전부 모킹 — 네트워크 의존 없음).

## 리스크
- 외부 모델 비결정성 — 테스트는 반드시 모킹. 실호출은 별도 통합 테스트(옵트인)로 분리.
- VLM 추출 품질 편차 — intent는 항상 stage-0 검증을 통과해야만 파이프라인 진입.
