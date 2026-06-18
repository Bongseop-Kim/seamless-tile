"""External-dependency adapters that live OUTSIDE the deterministic engine core.

These translate non-deterministic inputs (a text prompt via an LLM, a reference
image via a VLM + color quantization) into an engine ``intent`` JSON. The produced
intent is validated (stage-0) and frozen/cached so that everything downstream of the
adapter stays deterministic: the same request yields the same intent and therefore
the same SVG (ARCHITECTURE.md "외부 의존성 격리" / "결정론 경계").

Adapters never emit raw SVG or coordinates — only intent.
"""
