"""Shared test fakes."""


class _ScriptedLLM:
    """Returns canned completion strings in order (last one repeats)."""

    def __init__(self, *responses: str) -> None:
        if not responses:
            raise ValueError("_ScriptedLLM requires at least one response")
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[idx]
