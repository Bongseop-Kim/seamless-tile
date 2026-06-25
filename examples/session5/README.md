# 세션 5 미리보기 — 커버리지·scatter

세션 5에서 추가한 placement 기능을 실제 SVG/PNG로 보여주는 쇼케이스다.
모두 `default` colorway(네이비 ground / 코랄 accent / 골드 gold)로 렌더링했다.

## 재생성

```bash
.venv/bin/python examples/session5/generate.py
```

스크립트가 저장소 루트를 `sys.path`에 자동 추가하므로 `PYTHONPATH` 없이 어디서든 실행된다.
PNG는 `rsvg-convert`가 있을 때만 생성된다(없으면 건너뜀). 결과는 이 폴더에 떨어진다.

## 파일 구성

쇼케이스 1개당 3개 파일:

| 접미사 | 내용 |
|---|---|
| `*-tile.svg` | 단일 패턴 타일(`<pattern>` 1장) |
| `*-tiled.svg` | 4×4 반복본 — 브라우저로 열어 이음매(seam) 확인 |
| `*-tiled.png` | 4×4 반복본 200dpi 래스터(가장 보기 편함) |

## 쇼케이스 ↔ 기능

| 이름 | 기능 | 확인 포인트 |
|---|---|---|
| `01-lattice-halfdrop-dots` | lattice + `drop_fraction` | 홀짝 열이 반칸 내려간 half-drop 도트(비사선 올오버) |
| `02-scatter-poisson-bluenoise` | scatter `poisson` | seed 결정론 blue-noise, 최소거리 유지 |
| `03-scatter-sateen` | scatter `sateen` | 사틴 step 배열, 같은 행/열 정렬 없음 |
| `04-wave-lane-vine` | 곡선 `wave` lane | 사인 곡선 위 도트 + 곡선을 따라 회전하는 벌 |
| `06-point-set-anchors` | `point_set` | 명시 앵커점(모서리 4 + 중앙) 배치 |

## 새 패턴 추가

`generate.py`의 `SHOWCASES` 딕셔너리에 intent를 추가하면 된다. intent 계약은
저장소 루트의 `ARCHITECTURE.md` 참고.
