"""Conversational design sessions (session 16, P0).

An authoring-layer adapter over the deterministic engine: it adds ``session_id``, an
edit-as-delta turn model (LLM picks whitelisted tools, Python applies them), and a
confirm gate in front of the one expensive discrete op (Recraft motif generation). The
engine (intent → SVG) is never wrapped — it only ever sees a frozen, motif-resolved
intent. See ``docs/spec/conversational-design-sessions.md`` and
``docs/plan/16-conversational-sessions-p0.md``.
"""
