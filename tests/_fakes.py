"""Shared test fakes."""


class _ScriptedLLM:
    """Returns canned completion strings in order (last one repeats)."""

    def __init__(self, *responses: str) -> None:
        if not responses:
            raise ValueError("_ScriptedLLM requires at least one response")
        self._responses = list(responses)
        self.calls: list[str] = []
        # Per-call images (None for the text-only path); lets tests assert the route
        # threaded image bytes through to the multimodal seam.
        self.image_calls: list[list[bytes] | None] = []

    def complete(self, prompt: str, *, images: list[bytes] | None = None) -> str:
        self.calls.append(prompt)
        self.image_calls.append(images)
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[idx]
