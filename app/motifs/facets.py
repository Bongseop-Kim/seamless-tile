"""Motif facet helpers: controlled-vocab skeleton + deterministic variant_group key.

``variant_group`` groups variants of the same motif spec (spec §7.0, D16). It is a
**pure** function of the grouping facets (``subject`` + ``scope``) so two independent
implementations / processes derive the same key regardless of insertion order or
platform. ``subject`` is free text but stays in the key as the group's identity axis
(so a "pig" whole and a "desk" whole never share a pool). The hashed payload is
versioned (``"v"``) so a later session can fold ``expression`` into the core facet
without invalidating existing keys.

This module imports only the standard library (no DB / psycopg), so it can be unit
tested in isolation and imported by ``registry`` without pulling in the driver.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

# Controlled vocabulary (D10 / spec §5.1). `subject` is FREE TEXT: the domain is open
# (any object/shape/organism/abstract — a fixed noun list is impossible), so it has no
# vocab and no DB CHECK/FK; semantic discrimination is the embedding's job. Only `scope`
# (granularity) is controlled — it is the single retrieval hard filter, guarding the one
# failure embeddings can't catch: whole↔partial mismatch ("pig face" reused for "pig
# whole"). Anatomy/part names (wing, leg, ...) live in description+embedding, not here.
SCOPE_VOCAB: frozenset[str] = frozenset({"whole", "partial"})

# Bump VARIANT_GROUP_VERSION when the hashed payload shape changes; existing keys stay
# valid because the version is part of the hash. v2 renamed the `part` field to `scope`.
VARIANT_GROUP_VERSION = 2
VARIANT_GROUP_LEN = 16


def normalize_facet(value: str | None) -> str:
    """Canonical facet form for hashing/compare: NFC -> strip -> casefold.

    ``None`` and empty/whitespace-only values normalize to ``""``. The order is fixed
    (strip before casefold) so trailing whitespace never survives casefolding.
    """
    if value is None:
        return ""
    return unicodedata.normalize("NFC", value).strip().casefold()


def canonical_spec(spec: dict) -> dict:
    """The normalized facet subset that determines the rendered motif (cache key/freeze).

    Normalizing (NFC/strip/casefold) collapses trivial text variants to one frozen entry
    so equivalent specs reuse the same generated motif id. Shared by the LLM and Recraft
    motif adapters as their freeze/cache key.
    """
    return {
        k: normalize_facet(spec.get(k))
        for k in ("subject", "scope", "view", "expression", "style", "description")
    }


def variant_group_key(subject: str | None, scope: str | None) -> str:
    """Deterministic group key = ``sha256_hex(canonical(v, norm(subject), norm(scope)))``.

    Uses the same canonical-JSON serialization as ``determinism.layout_id_for`` /
    ``adapters.base.cache_key`` (``sort_keys=True``, compact separators), so equal
    facets always collide to the same key. ``subject`` is free text but kept here as the
    group identity axis; ``scope`` is the controlled granularity facet.
    """
    payload = {
        "v": VARIANT_GROUP_VERSION,
        "subject": normalize_facet(subject),
        "scope": normalize_facet(scope),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:VARIANT_GROUP_LEN]


_SCOPE_ALLOWED = frozenset(normalize_facet(s) for s in SCOPE_VOCAB)


def validate_facets(scope: str | None) -> None:
    """Controlled-vocab validation for the one controlled facet, ``scope`` (M2).

    ``subject`` is free text and intentionally unvalidated. ``scope`` is validated when
    the caller supplies one; ``None`` passes. Raises ``ValueError`` on an out-of-vocab
    value.
    """
    if scope is not None and normalize_facet(scope) not in _SCOPE_ALLOWED:
        raise ValueError(
            f"scope {scope!r} not in controlled vocabulary: {sorted(SCOPE_VOCAB)}"
        )
