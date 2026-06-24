import os
import subprocess
import sys
from pathlib import Path

from app.engine.placement import Instance
from app.engine.seamless import clone_instances
from app.motifs.registry import get_motif
from app.engine.generate import generate
from app.core.config import ENGINE_VERSION, REGISTRY_VERSION
from tests.test_intent import mvp_intent

TILE = 48.0
ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, hashseed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _with_alt_colorway():
    raw = mvp_intent()
    raw["colorways"].append(
        {
            "id": "alt",
            "name": "alt",
            "mapping": {"ground": "#000000", "accent": "#112233", "gold": "#445566"},
        }
    )
    return raw


def test_same_inputs_byte_identical_svg():
    a = generate(mvp_intent())
    b = generate(mvp_intent())
    assert a.svg == b.svg


def test_colorway_changes_bytes():
    raw = _with_alt_colorway()
    assert generate(raw, colorway_id="default").svg != generate(raw, colorway_id="alt").svg


def test_clone_instances_order_is_stable():
    motif = get_motif("circle")
    instances = [Instance(0.0, 0.0, 0.0), Instance(47.8, 24.0, 0.0), Instance(24.0, 24.0, 0.0)]
    first = clone_instances(instances, motif=motif, size_mm=1.0, tile_mm=TILE)
    second = clone_instances(instances, motif=motif, size_mm=1.0, tile_mm=TILE)
    assert first == second


def test_repro_meta_flows_through_generate():
    cand = generate(mvp_intent(), colorway_id="default")
    assert cand.repro.engine_version == ENGINE_VERSION
    assert cand.repro.registry_version == REGISTRY_VERSION
    assert cand.repro.colorway_id == "default"
    assert cand.repro.intent_version == 1
    assert cand.repro.seed == 184231  # mvp_intent seed


def test_seed_override_recorded():
    assert generate(mvp_intent(), seed=999).repro.seed == 999


def test_byte_identical_across_processes():
    # Guards against set/dict-iteration nondeterminism: same SVG under different
    # PYTHONHASHSEED values, in fresh interpreters.
    code = (
        "import sys; sys.path.insert(0, 'tests'); "
        "from test_intent import mvp_intent; "
        "from app.engine.generate import generate; "
        "sys.stdout.write(generate(mvp_intent()).svg)"
    )
    assert _run(code, "0") == _run(code, "1")


def test_candidate_set_byte_identical_across_processes():
    # The multi-design merge/select (round-robin + global SVG de-dup) must not leak
    # set/dict iteration order: same selection + order under different PYTHONHASHSEED.
    code = (
        "import sys; sys.path.insert(0, 'tests'); "
        "from test_intent import mvp_intent; "
        "from app.engine.candidates import generate_candidate_set; "
        "d1 = mvp_intent(); d2 = mvp_intent(); d2['layers'] = d2['layers'][:2]; "
        "cs = generate_candidate_set([d1, d2], candidate_count=4); "
        "sys.stdout.write('|'.join(c.id + ':' + c.candidate.svg for c in cs.candidates))"
    )

    assert _run(code, "0") == _run(code, "1") == _run(code, "12345")
