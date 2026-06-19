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


def test_settings_resource_ceiling_defaults():
    s = Settings(_env_file=None)
    assert s.max_placement_instances == 50_000
    assert s.max_svg_bytes == 2_000_000


def test_settings_validates_resource_ceilings():
    Settings(_env_file=None, max_placement_instances=1, max_svg_bytes=1)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_placement_instances=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_svg_bytes=0)
