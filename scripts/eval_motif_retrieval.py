"""Offline evaluation of the motif-retrieval cascade against a hand-labelled fixture.

Measures the PRODUCTION resolver cascade by importing the exact same helpers the
resolver uses (``app.adapters.motif_resolver._descriptor_text`` /
``._exact_match`` / ``._cosine``) rather than reimplementing the scoring logic, so a
report from this script reflects real resolver behavior, not a parallel approximation.

Deliberately OUT of scope: variant-pool sampling (``_select_variant``, spec §7.1) and
the Recraft image-generation miss path. Both are non-deterministic/expensive side
effects that don't affect the reuse-vs-generate decision this script is calibrating —
it only scores the pure text/vector retrieval stage (exact descriptor match -> scope
hard filter -> embedding cosine similarity against a tau threshold).

Embedding-model pin: vectors are cached in ``tests/fixtures/motif_eval/embeddings.json``
keyed only by text (not by model), with the model id recorded once at
``cache["model"]``. If ``EMBEDDING_MODEL`` changes, the cached vectors are no longer
comparable to a freshly-configured client -- regenerate them with ``--embed`` and
recalibrate tau; ``main()`` refuses to score against a stale cache (see the
model-mismatch check).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.adapters.embedding import client_from_settings  # noqa: E402
from app.adapters.motif_resolver import _cosine, _descriptor_text, _exact_match  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.motifs import facets  # noqa: E402
from app.motifs.store import MotifMeta  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "motif_eval"


def load_labelset() -> dict:
    """Read and parse the hand-authored fixture (corpus + queries)."""
    with open(FIXTURE_DIR / "labelset.json", encoding="utf-8") as f:
        return json.load(f)


def load_embeddings() -> dict:
    """Read the embedding cache, or an empty shell when it hasn't been generated yet.

    No I/O side effects beyond the read; never exits, never prints.
    """
    path = FIXTURE_DIR / "embeddings.json"
    if not path.exists():
        return {"model": None, "vectors": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _corpus_to_meta(row: dict) -> MotifMeta:
    return MotifMeta(
        id=row["id"],
        variant_group=None,
        subject=row.get("subject"),
        scope=row.get("scope"),
        view=row.get("view"),
        expression=row.get("expression"),
        style=row.get("style"),
        description=row.get("description"),
    )


def _meta_to_spec(rec: MotifMeta) -> dict:
    return {
        "subject": rec.subject,
        "scope": rec.scope,
        "view": rec.view,
        "expression": rec.expression,
        "style": rec.style,
        "description": rec.description,
    }


def missing_texts(labelset: dict, cache: dict) -> list[str]:
    """Descriptor texts (corpus rows + query specs) not yet present in ``cache["vectors"]``.

    Never raises for "missing" -- callers/tests use this list directly to decide whether
    an ``--embed`` pass is needed.
    """
    vectors = cache.get("vectors", {})
    texts: list[str] = []
    seen: set[str] = set()
    for row in labelset.get("corpus", []):
        text = _descriptor_text(row)
        if text not in seen:
            seen.add(text)
            if text not in vectors:
                texts.append(text)
    for query in labelset.get("queries", []):
        text = _descriptor_text(query.get("spec", {}))
        if text not in seen:
            seen.add(text)
            if text not in vectors:
                texts.append(text)
    return texts


def score_queries(labelset: dict, vectors: dict) -> list[dict]:
    """Run every query through the exact/scope-filter/vector cascade; no generation."""
    corpus = [_corpus_to_meta(row) for row in labelset.get("corpus", [])]
    results: list[dict] = []
    for query in labelset.get("queries", []):
        spec = query.get("spec", {})
        scope = facets.normalize_facet(spec.get("scope"))
        candidates = sorted(
            (rec for rec in corpus if facets.normalize_facet(rec.scope) == scope),
            key=lambda rec: rec.id,
        )
        row = {
            "name": query.get("name"),
            "category": query.get("category"),
            "expected": query.get("expected"),
        }
        exact = _exact_match(spec, candidates)
        if exact is not None:
            row.update(path="exact", best_id=exact, best_sim=None)
            results.append(row)
            continue

        query_text = _descriptor_text(spec)
        query_vec = vectors.get(query_text)
        best_id = None
        best_sim = None
        if query_vec is not None:
            for rec in candidates:
                cand_vec = vectors.get(_descriptor_text(_meta_to_spec(rec)))
                if not cand_vec or len(cand_vec) != len(query_vec):
                    continue
                sim = _cosine(query_vec, cand_vec)
                if best_sim is None or sim > best_sim:
                    best_id = rec.id
                    best_sim = sim
        if best_id is None:
            row.update(path="no-candidates", best_id=None, best_sim=None)
        else:
            row.update(path="vector", best_id=best_id, best_sim=best_sim)
        results.append(row)
    return results


def predict(row: dict, tau: float) -> str:
    """The reuse-vs-generate decision at a given similarity threshold."""
    if row["path"] == "exact":
        return row["best_id"]
    if row["path"] == "vector" and row["best_sim"] is not None and row["best_sim"] >= tau:
        return row["best_id"]
    return "generate"


def _acceptable_ids(expected) -> set[str] | None:
    """``None`` sentinel means "generate is correct"; else the set of acceptable ids."""
    if expected == "generate":
        return None
    if isinstance(expected, list):
        return set(expected)
    return {expected}


def metrics(scored: list[dict], tau: float) -> dict:
    """Confusion-matrix metrics at ``tau``. reuse = positive class."""
    tp = fp = fn = tn = 0
    for row in scored:
        acceptable = _acceptable_ids(row["expected"])
        prediction = predict(row, tau)
        if prediction != "generate":
            if acceptable is not None and prediction in acceptable:
                tp += 1
            else:
                fp += 1
        else:
            if acceptable is None:
                tn += 1
            else:
                fn += 1
    total = len(scored)
    return {
        "tau": tau,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if (tp + fp) else 1.0,
        "recall": tp / (tp + fn) if (tp + fn) else 1.0,
        "generate_rate": (fn + tn) / total if total else 0.0,
        "false_reuse_rate": fp / total if total else 0.0,
    }


def sweep(scored: list[dict]) -> None:
    """Print a tau curve: coarse 0.05 steps, refined to 0.01 steps in the transition
    region (the span of best_sim among vector-path rows) where metrics actually move."""
    vector_sims = [r["best_sim"] for r in scored if r["path"] == "vector"]
    if vector_sims:
        lo, hi = min(vector_sims), max(vector_sims)
    else:
        lo, hi = 1.0, 0.0  # empty region -> no fine grid

    taus: set[float] = {round(i / 100, 2) for i in range(0, 101, 5)}
    for i in range(0, 101):
        t = round(i / 100, 2)
        if lo <= t <= hi:
            taus.add(t)

    header = (
        f"{'tau':>6} {'precision':>10} {'recall':>8} {'gen_rate':>9} "
        f"{'false_reuse':>12} {'tp':>4} {'fp':>4} {'fn':>4} {'tn':>4}"
    )
    print(header)
    for t in sorted(taus):
        m = metrics(scored, t)
        print(
            f"{m['tau']:>6.2f} {m['precision']:>10.3f} {m['recall']:>8.3f} "
            f"{m['generate_rate']:>9.3f} {m['false_reuse_rate']:>12.3f} "
            f"{m['tp']:>4} {m['fp']:>4} {m['fn']:>4} {m['tn']:>4}"
        )


def recommend(scored: list[dict]) -> tuple[float, str]:
    """Recommend a tau and explain the reasoning."""
    impostor_sims = [
        r["best_sim"]
        for r in scored
        if r["expected"] == "generate" and r["path"] == "vector"
    ]
    genuine_sims = [
        r["best_sim"]
        for r in scored
        if r["path"] == "vector"
        and _acceptable_ids(r["expected"]) is not None
        and r["best_id"] in _acceptable_ids(r["expected"])
    ]
    s_imp = max(impostor_sims) if impostor_sims else None
    s_gen = min(genuine_sims) if genuine_sims else None

    lines: list[str] = []
    lines.append(f"impostor ceiling (s_imp) = {s_imp!r}")
    lines.append(f"genuine floor    (s_gen) = {s_gen!r}")
    if s_imp is not None and s_gen is not None:
        margin = s_gen - s_imp
        lines.append(f"margin (s_gen - s_imp)  = {margin:.4f}")
    else:
        lines.append("margin (s_gen - s_imp)  = n/a")

    if s_imp is not None and s_gen is not None and s_imp < s_gen:
        margin = s_gen - s_imp
        if margin < 0.05:
            tau = round(s_imp + 0.75 * margin, 2)
            lines.append(
                f"clean gap but narrow margin ({margin:.4f} < 0.05) -> biased upward"
            )
        else:
            tau = round((s_imp + s_gen) / 2, 2)
            lines.append("clean gap -> midpoint")
    else:
        # Fall back: smallest tau on the 0.01 grid with zero false reuse.
        best_recall_at_zero_fr = 0.0
        tau = 1.0
        found = False
        for i in range(0, 101):
            t = round(i / 100, 2)
            m = metrics(scored, t)
            if m["false_reuse_rate"] == 0.0:
                if not found:
                    tau = t
                    found = True
                best_recall_at_zero_fr = max(best_recall_at_zero_fr, m["recall"])
        if not found:
            tau = 1.0
        best_overall_recall = max(
            (metrics(scored, round(i / 100, 2))["recall"] for i in range(0, 101)),
            default=0.0,
        )
        lines.append(
            "no clean gap -> fallback to smallest tau on the 0.01 grid with "
            "false_reuse_rate == 0.0"
        )
        lines.append(
            f"recall sacrificed: {best_overall_recall - best_recall_at_zero_fr:.3f} "
            f"(recall at this tau = {best_recall_at_zero_fr:.3f}, "
            f"best achievable recall anywhere = {best_overall_recall:.3f})"
        )

    m_final = metrics(scored, tau)
    fp_names = [
        r["name"]
        for r in scored
        if predict(r, tau) != "generate"
        and (
            _acceptable_ids(r["expected"]) is None
            or predict(r, tau) not in _acceptable_ids(r["expected"])
        )
    ]
    fn_names = [
        r["name"]
        for r in scored
        if _acceptable_ids(r["expected"]) is not None and predict(r, tau) == "generate"
    ]
    lines.append(f"final tau = {tau}")
    lines.append(f"false-reuse (FP) at final tau: {fp_names}")
    lines.append(f"missed-reuse (FN) at final tau: {fn_names}")
    return tau, "\n".join(lines)


def _write_embeddings(cache: dict) -> None:
    """Hand-assemble compact JSON: one vector per line, no ``indent=`` (which would put
    one float per line and blow up the file size)."""
    path = FIXTURE_DIR / "embeddings.json"
    vectors = cache["vectors"]
    lines = ["{", f'  "model": {json.dumps(cache["model"])},', '  "vectors": {']
    for i, key in enumerate(sorted(vectors)):
        comma = "," if i < len(vectors) - 1 else ""
        vec_json = json.dumps(vectors[key], separators=(",", ":"))
        lines.append(f"    {json.dumps(key)}: {vec_json}{comma}")
    lines.append("  }")
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_embed(labelset: dict, cache: dict) -> dict:
    settings = get_settings()
    client = client_from_settings(settings)
    if client is None:
        print(
            "no embedding client configured (OPENAI_API_KEY unset) -- cannot run --embed"
        )
        sys.exit(2)
    to_embed = missing_texts(labelset, cache)
    if not to_embed:
        print("embeddings.json already covers every corpus/query text; nothing to do")
        return cache
    for text in to_embed:
        vec = client.embed(text)
        cache["vectors"][text] = [round(x, 6) for x in vec]
    cache["model"] = settings.embedding_model
    _write_embeddings(cache)
    print(f"embedded {len(to_embed)} new text(s); wrote {FIXTURE_DIR / 'embeddings.json'}")
    return cache


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--embed",
        action="store_true",
        help="fetch missing embeddings via OPENAI_API_KEY and update embeddings.json",
    )
    args = parser.parse_args(argv)

    labelset = load_labelset()
    cache = load_embeddings()

    if args.embed:
        cache = _run_embed(labelset, cache)

    settings = get_settings()
    if cache.get("model") and cache["model"] != settings.embedding_model:
        print(
            f"embedding model mismatch: cache={cache['model']!r}, "
            f"configured={settings.embedding_model!r} -- re-run --embed"
        )
        sys.exit(2)

    if not args.embed:
        missing = missing_texts(labelset, cache)
        if missing:
            print(f"missing {len(missing)} embedding(s):")
            for text in missing:
                print(f"  {text!r}")
            print("hint: run `python scripts/eval_motif_retrieval.py --embed`")
            sys.exit(2)

    scored = score_queries(labelset, cache["vectors"])
    sweep(scored)
    print()
    tau, explanation = recommend(scored)
    print(explanation)


if __name__ == "__main__":
    main()
