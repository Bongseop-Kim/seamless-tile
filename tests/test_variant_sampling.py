"""Session-11 seed variant sampling + descriptor text (spec §7.1, §9.7).

Pure-function tests for the determinism helpers and the resolver's embedding-source
text. No DB, no network.
"""

import pytest

from app.adapters.motif_resolver import _descriptor_text
from app.engine import determinism


# --- stable_hash ------------------------------------------------------------


def test_stable_hash_deterministic_and_distinct():
    assert determinism.stable_hash("g:1") == determinism.stable_hash("g:1")
    assert determinism.stable_hash("g:1") != determinism.stable_hash("g:2")
    assert isinstance(determinism.stable_hash("x"), int)


# --- select_variant ---------------------------------------------------------


def test_select_variant_seed_varies_choice():
    # Criterion 3: with a pool >= 2, varying the seed reaches every variant.
    pool = ["m-a", "m-b"]
    seen = {determinism.select_variant(pool, "g", s) for s in range(20)}
    assert seen == {"m-a", "m-b"}


def test_select_variant_invariant_to_pool_order():
    # Criterion 4: the choice depends on the sorted pool, not the input order.
    a = determinism.select_variant(["m-c", "m-a", "m-b"], "g", 5)
    b = determinism.select_variant(["m-b", "m-c", "m-a"], "g", 5)
    assert a == b


def test_select_variant_same_seed_is_stable():
    pool = ["m-a", "m-b", "m-c"]
    assert determinism.select_variant(pool, "g", 7) == determinism.select_variant(pool, "g", 7)


def test_select_variant_single_pool_is_that_one():
    assert determinism.select_variant(["only"], "g", 99) == "only"


def test_select_variant_empty_pool_raises():
    with pytest.raises(ValueError):
        determinism.select_variant([], "g", 0)


def test_select_variant_group_changes_choice_independently_of_seed():
    # Different groups hash independently, so the same seed can pick different indices.
    pool = ["m-a", "m-b"]
    groups = {determinism.select_variant(pool, f"grp{i}", 0) for i in range(20)}
    assert groups == {"m-a", "m-b"}


# --- _descriptor_text -------------------------------------------------------


def test_descriptor_text_prefers_description():
    spec = {"description": "smiling pig face", "subject": "pig", "part": "face"}
    assert _descriptor_text(spec) == "smiling pig face"


def test_descriptor_text_synthesizes_from_facets():
    spec = {"subject": "pig", "part": "face", "view": "front", "style": "flat",
            "expression": "smiling"}
    assert _descriptor_text(spec) == "smiling pig face, front view, flat"


def test_descriptor_text_drops_empty_facets_no_dangling_punctuation():
    text = _descriptor_text({"subject": "pig", "part": "face"})
    assert text == "pig face"
    assert "  " not in text  # no double spaces from missing facets
    assert not text.endswith(",") and ", ," not in text


def test_descriptor_text_blank_description_falls_through_to_facets():
    spec = {"description": "   ", "subject": "pig", "part": "face"}
    assert _descriptor_text(spec) == "pig face"
