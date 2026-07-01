#!/usr/bin/env python3
"""Intent JSON 갤러리 렌더러 (API/DB/LLM 미사용).

`gallery/json/*.json`(소스)을 **기존 결정론 엔진**(`app.engine.generate.generate`)으로 직접 렌더해
`gallery/svg/<slug>.svg`를 떨군다. 이어서 **원단 텍스처 렌더**(`render_fabric`)로 `gallery/png/<slug>.png`도
만든다 — intent의 `production.method`(print=균일 twill / yarn_dyed=영역별)를 따르며, json에 `_finalize`
사이드카가 있으면 weave·material_map을 오버라이드한다. 미세조정은 json을 직접 편집하고 다시 실행하면
된다(svg/·png/는 출력물 — gitignore). 새 패턴은 json 새 파일로 추가.

모티프 '모양'은 변주 대상이 아니다(창작 영역). motif layer가 있는 json은
`scripts/gallery_assets/*.svg`를 in-memory 등록해 쓴다. json의 motif_id는 asset 파일명
stem(예: `crest.svg` -> `crest`)이나 실제 등록 id를 참조할 수 있다. 알 수 없는 id는
첫 asset(`default`)로 치환해 기존 gallery json을 계속 렌더한다. asset이 없으면 motif
json은 스킵하고 배경/stripe json만 렌더한다.

실행:  .venv/bin/python scripts/gallery.py
검증:  같은 json은 바이트 동일 svg(결정론).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# scripts/ 는 sys.path[0]이 되지만 repo 루트는 아니다 → app/ 임포트를 위해 루트 추가.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.adapters.recraft import _flatten_unsuitable  # noqa: E402
from app.engine.generate import generate  # noqa: E402
from app.engine.units import fmt  # noqa: E402
from app.motifs.registry import normalize_motif_svg, register_motif  # noqa: E402
from app.motifs.store import set_default_store  # noqa: E402
from app.render.fabric import FabricError, available_weaves, render_fabric  # noqa: E402
from app.render.raster import RasterError, find_renderer  # noqa: E402

DISPLAY_SCALE = 4  # 표시 배율: viewBox는 그대로 두고 svg width/height(mm)만 키운다
ASSETS_DIR = _ROOT / "scripts" / "gallery_assets"
GALLERY_DIR = _ROOT / "gallery"
JSON_DIR = GALLERY_DIR / "json"  # 소스 intent (git 추적)
SVG_DIR = GALLERY_DIR / "svg"    # 출력 svg (gitignore)
PNG_DIR = GALLERY_DIR / "png"    # 출력 fabric png (gitignore)

# 사이드카 키: 엔진 intent가 아니라 finalize(원단 텍스처 렌더) 파라미터. Intent는 extra="forbid"라
# generate 전에 반드시 pop한다. 값 예: {"production_method","weave","material_map","colorway_id","dpi"}.
FINALIZE_KEY = "_finalize"

# 텍스처 정책(사이드카 없는 디자인의 기본): 아래 번호는 print(균일 twill-45), 나머지는 yarn-dyed로
# color slot마다 weave를 순환 배정한다. 명시적으로 바꾸려면 해당 json에 `_finalize`를 넣으면 된다.
PRINT_ONLY = {"07", "09", "23"}
# yarn-dyed 영역별 weave 순환(자산 7종 모두 골고루 — check 포함). 순서만 바뀌어도 결과가 달라진다.
YARN_WEAVE_CYCLE = ("twill-45", "solid", "herringbone", "pindot", "twill-0", "jacquard", "check")


def _auto_material_map(intent: dict, offset: int = 0) -> dict[str, str]:
    """디자인의 color slot들(palette 선언 순서)에 yarn-dyed weave를 결정론적으로 순환 배정.
    offset은 디자인마다 순환 시작점을 옮겨 갤러리 전체에서 7종이 골고루 쓰이게 한다."""
    avail = available_weaves()
    cycle = [w for w in YARN_WEAVE_CYCLE if w in avail] or list(avail)
    slots = [s["id"] for s in intent.get("palette", {}).get("slots", [])]
    return {sid: cycle[(offset + i) % len(cycle)] for i, sid in enumerate(slots)}


def _load_gallery_motifs() -> tuple[dict[str, str], list[tuple[str, str]]]:
    """gallery_assets/*.svg 전체를 in-memory 등록하고 alias -> motif_id 맵 반환.

    Alias는 asset stem과 실제 등록 id 둘 다 지원한다. `default`는 이름 정렬 첫 asset이다.
    _flatten_unsuitable로 full-canvas 배경 제거, render_check=False로 librsvg 의존을 피한다.
    """
    if not ASSETS_DIR.is_dir():
        return {}, []
    svgs = sorted(ASSETS_DIR.glob("*.svg"))
    if not svgs:
        return {}, []

    aliases: dict[str, str] = {}
    registered: list[tuple[str, str]] = []
    for svg_path in svgs:
        try:
            raw = _flatten_unsuitable(svg_path.read_text(encoding="utf-8"))
            motif = normalize_motif_svg(raw, max_color_slots=6, render_check=False)
            motif_id = register_motif(motif, source="gallery")
        except Exception as exc:  # noqa: BLE001 — 한 asset 실패가 나머지 등록을 막지 않게
            print(f"  warn asset {svg_path.stem}: {exc!r} — skipped", file=sys.stderr)
            continue
        aliases.setdefault(svg_path.stem, motif_id)
        aliases[motif_id] = motif_id
        aliases.setdefault("default", motif_id)
        registered.append((svg_path.stem, motif_id))
    return aliases, registered


def _scale_svg(svg: str, tile_mm: float) -> str:
    """viewBox는 두고 root width/height(mm)만 DISPLAY_SCALE배로."""
    disp = fmt(tile_mm * DISPLAY_SCALE)
    return svg.replace(
        f'width="{fmt(tile_mm)}mm" height="{fmt(tile_mm)}mm"',
        f'width="{disp}mm" height="{disp}mm"', 1)


def _render_fabric(intent: dict, cfg: dict, stem: str) -> str | None:
    """`_finalize` 사이드카가 있으면 결정론 원단 텍스처 PNG(`<stem>.fabric.png`)를 떨군다.

    intent는 (motif_id 치환·사이드카 pop이 끝난) 엔진 intent 그대로 넘긴다 — render_fabric이
    같은 결정론 경로로 재검증·재합성·래스터화한다. 렌더러가 없으면 SVG만 남기고 스킵한다.
    반환: 상태 메시지(없으면 None). 예외는 호출부에서 파일 실패로 처리하지 않고 여기서 흡수.
    """
    if find_renderer() is None:
        return "no SVG renderer — fabric skipped"
    method = cfg.get("production_method")
    try:
        png = render_fabric(
            intent,
            colorway_id=cfg.get("colorway_id"),
            production_method=method,
            weave=cfg.get("weave", "twill-45"),
            material_map=cfg.get("material_map"),
            dpi=cfg.get("dpi"),
            texture_strength=cfg.get("texture_strength"),
            relief_strength=cfg.get("relief_strength"),  # yarn_dyed: raised-thread emboss (None => default on)
        )
    except (FabricError, RasterError, ValueError) as exc:  # noqa: BLE001
        return f"fabric FAILED: {exc}"
    (PNG_DIR / f"{stem}.png").write_bytes(png)
    label = method or "intent"
    return f"fabric ok ({label}, {len(png)} bytes)"


def main() -> int:
    # DB 저장 차단 보증: store=None이면 register_motif write-through·motif lazy-load 모두
    # no-op(in-memory only). SUPABASE_DB_URL이 설정돼 있어도 무시한다.
    if os.environ.get("SUPABASE_DB_URL"):
        print("warning: SUPABASE_DB_URL is set; ignoring it (gallery never touches the DB)",
              file=sys.stderr)
    set_default_store(None)

    SVG_DIR.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(JSON_DIR.glob("*.json")) if JSON_DIR.is_dir() else []
    if not files:
        print(f"no *.json in {JSON_DIR}", file=sys.stderr)
        return 1

    motif_aliases, registered_motifs = _load_gallery_motifs()
    if registered_motifs:
        print("gallery motifs registered:")
        for alias, motif_id in registered_motifs:
            print(f"  {alias}: {motif_id}")
    else:
        print(f"no SVG assets in {ASSETS_DIR}/ — motif json will be skipped", file=sys.stderr)

    failures: list[tuple[str, str]] = []
    skipped: list[str] = []
    fabric_msgs: list[str] = []
    for p in files:
        try:
            intent = json.loads(p.read_text(encoding="utf-8"))
            finalize_cfg = intent.pop(FINALIZE_KEY, None)  # 엔진 intent 밖의 텍스처 파라미터
            layers = intent.get("layers", [])
            motif_layers = [L for L in layers if L.get("type") == "motif"]
            if motif_layers:
                if not motif_aliases:
                    skipped.append(p.stem)
                    continue
                for L in motif_layers:
                    requested = L.get("params", {}).get("motif_id", "default")
                    motif_id = motif_aliases.get(requested)
                    if motif_id is None:
                        motif_id = motif_aliases["default"]
                        print(
                            f"  warn {p.stem}: unknown motif_id {requested!r}; "
                            f"using default motif {motif_id}",
                            file=sys.stderr,
                        )
                    L["params"]["motif_id"] = motif_id
            cand = generate(intent)
            svg = _scale_svg(cand.svg, float(intent["canvas"]["tile_mm"]))
            (SVG_DIR / f"{p.stem}.svg").write_text(svg, encoding="utf-8")
            # 텍스처 렌더는 갤러리 전체에 적용한다. 사이드카(_finalize)가 있으면 그대로,
            # 없으면 기본 정책: PRINT_ONLY는 print(균일 twill-45), 나머지는 yarn-dyed(영역별).
            if finalize_cfg is None:
                if p.stem.split("_", 1)[0] in PRINT_ONLY:
                    finalize_cfg = {}  # print / uniform twill-45
                else:
                    offset = int(p.stem.split("_", 1)[0])  # 디자인마다 순환 시작점 이동
                    finalize_cfg = {
                        "production_method": "yarn_dyed",
                        "material_map": _auto_material_map(intent, offset),
                    }
            fabric_status = _render_fabric(intent, finalize_cfg, p.stem)
        except Exception as exc:  # noqa: BLE001 — 한 json 실패가 나머지를 막지 않게
            failures.append((p.stem, repr(exc)))
            print(f"  FAIL {p.stem}: {exc}", file=sys.stderr)
            continue
        warn = f"  ({len(cand.warnings)} warning(s))" if cand.warnings else ""
        print(f"  ok   {p.stem}{warn}")
        for w in cand.warnings:
            print(f"         - {w}")
        if fabric_status is not None:
            print(f"         · {fabric_status}")
            fabric_msgs.append(f"{p.stem}: {fabric_status}")

    rendered = len(files) - len(failures) - len(skipped)
    print(f"\n{rendered}/{len(files)} rendered → {SVG_DIR}/ + {PNG_DIR}/")
    if fabric_msgs:
        print(f"fabric texture renders: {len(fabric_msgs)}")
    if skipped:
        print(f"skipped (need dummy SVG): {skipped}", file=sys.stderr)
    if failures:
        print(f"{len(failures)} FAILED: {[s for s, _ in failures]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
