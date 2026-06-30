import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app


def test_settings_validates_motif_similarity_tau():
    Settings(_env_file=None, motif_similarity_tau=0.0)
    Settings(_env_file=None, motif_similarity_tau=1.0)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, motif_similarity_tau=-0.01)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, motif_similarity_tau=1.01)


def test_settings_validates_recraft_max_color_slots():
    Settings(_env_file=None, recraft_max_color_slots=1)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, recraft_max_color_slots=0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_settings_rejects_non_finite_motif_max_aspect_ratio(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, motif_max_aspect_ratio=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_settings_rejects_non_finite_motif_edge_seam_tol(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, motif_edge_seam_tol=value)


def test_settings_resource_ceiling_defaults():
    s = Settings(_env_file=None)
    assert s.max_placement_instances == 50_000
    assert s.max_svg_bytes == 2_000_000
    assert s.preview_dpi == 192


def test_settings_validates_resource_ceilings():
    Settings(
        _env_file=None, max_placement_instances=1, max_svg_bytes=1, preview_dpi=1200
    )

    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_placement_instances=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_svg_bytes=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, preview_dpi=1201)


def test_startup_requires_model_keys(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    monkeypatch.setenv("OPENAI_API_KEY", "\t")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY.*OPENAI_API_KEY"):
        with TestClient(create_app()):
            pass
    get_settings.cache_clear()


def test_startup_accepts_required_model_keys(monkeypatch):
    from app.adapters.embedding import set_default_embedding_client
    from app.adapters.llm import set_default_client
    from app.core.config import get_settings

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("app.main.client_from_settings", lambda settings: object())
    monkeypatch.setattr(
        "app.main.embedding_client_from_settings", lambda settings: object()
    )
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert client.get("/api/v1/health").status_code == 200
    finally:
        set_default_client(None)
        set_default_embedding_client(None)
        get_settings.cache_clear()
