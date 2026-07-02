# 세션 18 — 분기 / undo / redo / fork (time-travel) [P2]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/conversational-design-sessions.md` — §5(체크포인트=턴 경계), §10(`from_checkpoint`, MVP 이후),
  §13(LangGraph 채택 근거), 결정 **S9·S14**.
- **현재 코드**: 세션 16(그래프·checkpoint) + 세션 17(영속 checkpointer).
- **선행 세션**: **세션 16(P0)**, **세션 17(P1)**.
- **⚠ 스키마**: time-travel은 세션 17의 **client-only** checkpoint 백엔드 위에서 돈다. 체크포인트 테이블은
  모노레포에 이미 존재해야 하고, 이 레포는 `setup()`/DDL을 실행하지 않는다. 분기 정리/보존 정책도 **DDL 없이**(모노레포와 협의).

## 목표
**확정 제품 기능**(S14)인 되감기·가지치기를 LangGraph checkpoint로 노출한다. 각 턴 경계가 체크포인트이므로
`(thread_id=session_id, checkpoint_id)`로 특정 턴으로 되감고, 그 지점에서 **fork**(대안 분기)한다.

## 범위
- LangGraph `get_state_history`(체크포인트 열거) + `update_state`/`from_checkpoint` 재개로 **undo/redo/fork** 노출.
- API: `POST /generate`·세션 액션에 `from_checkpoint` 옵션 — 지정 체크포인트에서 편집 턴 재개.
- 복원된/분기된 intent 재합성이 원 지점과 **byte 동일**(결정론 유지).

## 비범위
- 트레이싱·eval(§12 LangSmith, 선택) — 별도 후속(§15 P3).
- planner/critic 멀티에이전트(§15 후보) — 별도 spec·ROI 검증.
- 새 UI — API 표면만.

## 작업 / 만들·수정 파일
- `app/sessions/*`: checkpoint 열거/재개/fork 헬퍼(LangGraph `get_state_history`·`update_state` 래핑).
- `app/api/routes/sessions.py`(또는 `generate.py`): `from_checkpoint` 파라미터 + fork 엔드포인트.

## 수용 기준 (검증 가능)
1. **되감기**: 특정 turn 체크포인트로 되감아 그 시점 `current_intent`를 복원, 재합성이 원본과 byte 동일.
2. **fork**: 되감은 지점에서 다른 편집을 적용하면 원 분기를 훼손하지 않고 새 후보 생성.
3. **redo**: 되감기 후 앞으로 감기(다음 체크포인트 복원) 동작.
4. **결정론 무손상**: fork/undo 경로에서도 `tests/test_determinism.py` 그린.

## 리스크
- checkpoint 저장 백엔드(세션 17)와의 정합·용량. 분기 트리 폭주 시 정리 정책 필요.
- LangGraph time-travel API 버전 churn — authoring 계층에 격리.

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + verifier.
