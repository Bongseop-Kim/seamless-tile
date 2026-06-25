#!/usr/bin/env python3
"""Intent JSON 갤러리 렌더러 (API/DB/LLM 미사용).

`gallery/*.json`(소스)을 **기존 결정론 엔진**(`app.engine.generate.generate`)으로
직접 렌더해 같은 폴더에 `<slug>.svg`를 떨군다. 미세조정은 json을 직접 편집하고 다시
실행하면 된다(svg는 출력물 — gitignore). 새 패턴은 json 새 파일로 추가.

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

DISPLAY_SCALE = 4  # 표시 배율: viewBox는 그대로 두고 svg width/height(mm)만 키운다
ASSETS_DIR = _ROOT / "scripts" / "gallery_assets"
GALLERY_DIR = _ROOT / "gallery"


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
        raw = _flatten_unsuitable(svg_path.read_text(encoding="utf-8"))
        motif = normalize_motif_svg(raw, max_color_slots=6, render_check=False)
        motif_id = register_motif(motif, source="gallery")
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


def main() -> int:
    # DB 저장 차단 보증: store=None이면 register_motif write-through·motif lazy-load 모두
    # no-op(in-memory only). SUPABASE_DB_URL이 설정돼 있어도 무시한다.
    if os.environ.get("SUPABASE_DB_URL"):
        print("warning: SUPABASE_DB_URL is set; ignoring it (gallery never touches the DB)",
              file=sys.stderr)
    set_default_store(None)

    files = sorted(GALLERY_DIR.glob("*.json")) if GALLERY_DIR.is_dir() else []
    if not files:
        print(f"no *.json in {GALLERY_DIR}", file=sys.stderr)
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
    for p in files:
        intent = json.loads(p.read_text(encoding="utf-8"))
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
        try:
            cand = generate(intent)
        except Exception as exc:  # noqa: BLE001 — 한 json 실패가 나머지를 막지 않게
            failures.append((p.stem, repr(exc)))
            print(f"  FAIL {p.stem}: {exc}", file=sys.stderr)
            continue
        svg = _scale_svg(cand.svg, float(intent["canvas"]["tile_mm"]))
        (GALLERY_DIR / f"{p.stem}.svg").write_text(svg, encoding="utf-8")
        warn = f"  ({len(cand.warnings)} warning(s))" if cand.warnings else ""
        print(f"  ok   {p.stem}{warn}")
        for w in cand.warnings:
            print(f"         - {w}")

    rendered = len(files) - len(failures) - len(skipped)
    print(f"\n{rendered}/{len(files)} rendered to {GALLERY_DIR}")
    if skipped:
        print(f"skipped (need dummy SVG): {skipped}", file=sys.stderr)
    if failures:
        print(f"{len(failures)} FAILED: {[s for s, _ in failures]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
