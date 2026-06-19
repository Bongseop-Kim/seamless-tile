"""Session-9 facet helpers: deterministic variant_group key + normalization + vocab.

Pure unit tests — no DB. Pins the documented normalization rule (NFC -> strip ->
casefold) and the determinism contract for variant_group (spec section 7.0, D16).
"""

import unicodedata

import pytest

from app.motifs.facets import (
    VARIANT_GROUP_LEN,
    normalize_facet,
    validate_facets,
    variant_group_key,
)


def test_variant_group_deterministic_same_input():
    assert variant_group_key("pig", "whole") == variant_group_key("pig", "whole")


def test_variant_group_normalization_case_and_whitespace():
    assert variant_group_key("  PIG ", "Whole") == variant_group_key("pig", "whole")


def test_variant_group_normalization_unicode_nfc():
    # Built from code points (ASCII source) so the two forms are genuinely distinct
    # regardless of how this file is saved: 'e' + U+0301 (NFD) vs the NFC collapse.
    decomposed = "cafe" + chr(0x0301)  # 'e' + combining acute accent
    composed = unicodedata.normalize("NFC", decomposed)  # single precomposed code point
    assert composed != decomposed  # genuinely different code-point sequences
    assert variant_group_key(composed, "whole") == variant_group_key(decomposed, "whole")


def test_variant_group_distinct_facets_differ():
    assert variant_group_key("pig", "whole") != variant_group_key("pig", "partial")
    assert variant_group_key("pig", "whole") != variant_group_key("cow", "whole")


def test_variant_group_none_equals_empty():
    assert variant_group_key(None, None) == variant_group_key("", "")


def test_variant_group_shape_is_hex_of_fixed_len():
    key = variant_group_key("pig", "whole")
    assert len(key) == VARIANT_GROUP_LEN
    assert all(c in "0123456789abcdef" for c in key)


def test_normalize_facet_none_and_blank_and_trim():
    assert normalize_facet(None) == ""
    assert normalize_facet("   ") == ""
    assert normalize_facet(" Foo ") == "foo"


def test_validate_facets_rejects_out_of_vocab_scope():
    with pytest.raises(ValueError):
        validate_facets("banana")


def test_validate_facets_accepts_known_scope_and_none():
    validate_facets("whole")  # in SCOPE_VOCAB
    validate_facets("partial")  # in SCOPE_VOCAB
    validate_facets(None)  # unspecified is allowed
