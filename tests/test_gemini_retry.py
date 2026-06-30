"""GeminiClient transient-error retry: 503/429 are retried with backoff, other
codes fail fast. sleep is monkeypatched so the test never actually waits."""

import pytest

from app.adapters import gemini
from app.adapters.base import AdapterClientError
from app.adapters.gemini import GeminiClient

errors = gemini.errors


class _FakeModels:
    def __init__(self, errors_then_ok):
        self._script = list(errors_then_ok)  # exceptions to raise, then None=succeed
        self.calls = 0

    def generate_content(self, **_):
        self.calls += 1
        exc = self._script.pop(0) if self._script else None
        if exc is not None:
            raise exc
        return type("R", (), {"text": "ok"})()


def _client(script):
    c = GeminiClient.__new__(GeminiClient)  # skip __init__ (no real SDK client)
    c._model, c._temperature = "m", 0.0
    c._client = type("C", (), {"models": _FakeModels(script)})()
    return c


def _api_error(code):
    return errors.APIError(code, {"error": {"message": f"err {code}"}})


def test_retries_503_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    c = _client([_api_error(503), _api_error(503), None])
    assert c.complete("p") == "ok"
    assert c._client.models.calls == 3
    assert sleeps == [0.5, 1.0]  # exponential backoff between the 3 attempts


def test_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(gemini.time, "sleep", lambda _: None)
    c = _client([_api_error(503)] * gemini._MAX_ATTEMPTS)
    with pytest.raises(AdapterClientError):
        c.complete("p")
    assert c._client.models.calls == gemini._MAX_ATTEMPTS


def test_non_retryable_fails_fast(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gemini.time, "sleep", sleeps.append)
    c = _client([_api_error(400), None])
    with pytest.raises(AdapterClientError):
        c.complete("p")
    assert c._client.models.calls == 1  # no retry on 400
    assert sleeps == []
