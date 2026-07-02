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


class _ScriptedRecraft:
    """Returns canned SVGs in order (last repeats); records calls. Mirrors _ScriptedLLM
    but exposes the RecraftClient ``.generate(prompt)`` seam the miss path drives."""

    def __init__(self, *svgs: str) -> None:
        if not svgs:
            raise ValueError("_ScriptedRecraft requires at least one SVG")
        self._svgs = list(svgs)
        self.calls: list[str] = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._svgs[min(len(self.calls) - 1, len(self._svgs) - 1)]


class _ScriptedEditLLM:
    """Returns canned edit tool-call lists in order (last repeats); records calls. Mirrors
    the ``EditLLM.propose(summary, instruction) -> list[{name, args}]`` seam so session
    edit turns run without a real bind_tools LLM."""

    def __init__(self, *tool_call_lists: list[dict]) -> None:
        self._lists = list(tool_call_lists) or [[]]
        self.calls: list[tuple[str, str]] = []

    def propose(self, summary: str, instruction: str) -> list[dict]:
        self.calls.append((summary, instruction))
        return self._lists[min(len(self.calls) - 1, len(self._lists) - 1)]
