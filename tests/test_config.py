from pathlib import Path

import pytest
from pydantic import ValidationError

from trampo.config import load_config, load_profile, private_paths


def test_loads_repo_config() -> None:
    config = load_config()
    assert len(config.tracks) == 2
    assert config.max_age_days == 7
    assert config.auto_resume_min_fit_score == 8


def test_missing_field_names_it(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("""
max_age_days = 7
discovery_max_searches_per_day = 10
auto_resume_min_fit_score = 8
ignore_title_keywords = []
tracks = []
""")

    with pytest.raises(ValidationError, match="repost_days"):
        load_config(path)


def test_loads_example_profile() -> None:
    profile = load_profile(Path("private.example/profile.toml"))
    assert profile.backup_dir is None


def test_private_dir_from_env() -> None:
    paths = private_paths({"TRAMPO_PRIVATE_DIR": "/x"})
    assert paths.db == Path("/x/trampo.db")
    assert private_paths({}).private_dir == Path("private")
