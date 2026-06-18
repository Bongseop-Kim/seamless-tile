"""Motif facet helpers: controlled-vocab skeleton + deterministic variant_group key.

``variant_group`` groups variants of the same motif spec (spec §7.0, D16). It is a
**pure** function of the controlled facets (``subject``, ``part``) so two independent
implementations / processes derive the same key regardless of insertion order or
platform. The hashed payload is versioned (``"v"``) so a later session can fold
``expression`` into the core facet without invalidating existing keys.

This module imports only the standard library (no DB / psycopg), so it can be unit
tested in isolation and imported by ``registry`` without pulling in the driver.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

# Controlled-vocabulary skeleton (D10 / spec §5.1). P0 seeds a minimal `part` set;
# `subject` is left open because there is no source catalog yet (spec §12). The real
# vocab + DB CHECK/FK lands when the facet-extraction glue arrives (S11+).
SUBJECT_VOCAB: frozenset[str] = frozenset()
PART_VOCAB: frozenset[str] = frozenset(
    {"whole", "face", "head", "feet", "body", "wing", "tail"}
)

# Bump VARIANT_GROUP_VERSION when the hashed payload shape changes (e.g. folding in
# expression); existing v1 keys stay valid because the version is part of the hash.
VARIANT_GROUP_VERSION = 1
VARIANT_GROUP_LEN = 16


def normalize_facet(value: str | None) -> str:
    """Canonical facet form for hashing/compare: NFC -> strip -> casefold.

    ``None`` and empty/whitespace-only values normalize to ``""``. The order is fixed
    (strip before casefold) so trailing whitespace never survives casefolding.
    """
    if value is None:
        return ""
    return unicodedata.normalize("NFC", value).strip().casefold()


def variant_group_key(subject: str | None, part: str | None) -> str:
    """Deterministic group key = ``sha256_hex(canonical(v, norm(subject), norm(part)))``.

    Uses the same canonical-JSON serialization as ``determinism.layout_id_for`` /
    ``adapters.base.cache_key`` (``sort_keys=True``, compact separators), so equal
    facets always collide to the same key.
    """
    payload = {
        "v": VARIANT_GROUP_VERSION,
        "subject": normalize_facet(subject),
        "part": normalize_facet(part),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:VARIANT_GROUP_LEN]


def validate_facets(subject: str | None, part: str | None) -> None:
    """Basic controlled-vocab validation skeleton (P0).

    Validates ``part`` only when the caller supplies one and ``PART_VOCAB`` is
    non-empty; ``subject`` is open in P0 (no catalog). ``None`` passes. Raises
    ``ValueError`` on an out-of-vocab value.
    """
    if part is not None and PART_VOCAB:
        allowed = {normalize_facet(p) for p in PART_VOCAB}
        if normalize_facet(part) not in allowed:
            raise ValueError(
                f"part {part!r} not in controlled vocabulary: {sorted(PART_VOCAB)}"
            )
    if subject is not None and SUBJECT_VOCAB:
        allowed = {normalize_facet(s) for s in SUBJECT_VOCAB}
        if normalize_facet(subject) not in allowed:
            raise ValueError(
                f"subject {subject!r} not in controlled vocabulary: {sorted(SUBJECT_VOCAB)}"
            )
