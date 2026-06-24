"""Gemini chat-LLM client (D12): a thin :class:`~app.adapters.llm.LLMClient`.

Real network calls are opt-in — :func:`app.main.lifespan` installs this as the default
LLM client only when ``GEMINI_API_KEY`` is configured. The ``google-genai`` SDK is
imported lazily so this module (and the test suite) import without the dependency or a
key present. All SDK/network failures are normalized to
:class:`~app.adapters.base.AdapterClientError` (the route maps that to 502).
"""

from __future__ import annotations

from app.adapters.base import AdapterClientError

DEFAULT_MODEL = "gemini-2.5-flash-lite"


class GeminiClient:
    """Adapts ``google-genai`` to the ``complete(prompt) -> str`` seam.

    ``temperature`` defaults to 0 to minimize variance; the adapter still freezes the
    finalized output in its cache, so the determinism contract does not depend on it.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        temperature: float = 0.0,
    ) -> None:
        if not api_key:
            raise AdapterClientError("GeminiClient requires a non-empty api_key")
        self._model = model
        self._temperature = temperature
        try:
            from google import genai
        except ImportError as exc:  # dependency not installed
            raise AdapterClientError(f"google-genai is not installed: {exc}") from exc
        self._client = genai.Client(api_key=api_key)

    def complete(self, prompt: str) -> str:
        from google.genai import errors, types

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=self._temperature),
            )
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", str(exc))
            raise AdapterClientError(f"Gemini API error ({code}): {message}") from exc
        except Exception as exc:  # transport / unexpected SDK failure
            raise AdapterClientError(f"Gemini request failed: {exc}") from exc

        text = response.text
        if not text:
            raise AdapterClientError("Gemini returned an empty response")
        return text


def client_from_settings(settings) -> GeminiClient | None:
    """Build a :class:`GeminiClient` iff ``gemini_api_key`` is set, else ``None``."""
    api_key = getattr(settings, "gemini_api_key", None)
    if not api_key:
        return None
    model = getattr(settings, "gemini_model", None) or DEFAULT_MODEL
    temperature = getattr(settings, "gemini_temperature", 0.0)
    return GeminiClient(api_key, model, temperature=temperature)
