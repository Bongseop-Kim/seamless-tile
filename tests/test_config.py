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
