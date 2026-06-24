import pytest
from pydantic import ValidationError

from app.core.config import Settings


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
