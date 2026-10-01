"""Load config.toml (public) and private/profile.toml (Salary floor, accepted
contracts), and resolve the private data paths."""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, model_validator

TrackId = Literal["backend", "agents"]


class Track(BaseModel):
    id: TrackId
    name: str
    title_keywords: list[str]
    title_keywords_requiring_go: list[str] = []  # match only if the description mentions Go
    emphasis: str  # what the Tailored resume highlights


class Config(BaseModel):
    max_age_days: int
    repost_days: int
    discovery_max_searches_per_day: int
    auto_resume_min_fit_score: int
    auto_dismiss_max_fit_score: int
    ignore_title_keywords: list[str]
    tracks: list[Track]


class SalaryFloor(BaseModel):
    clt_brl: int
    pj_brl: int
    international_usd: int


class Profile(BaseModel):
    accepted_contracts: list[Literal["clt", "pj", "international_contractor"]]
    salary_floor: SalaryFloor
    backup_dir: Path | None

    @model_validator(mode="before")
    @classmethod
    def _flatten_backup_dir(cls, data: Any) -> Any:
        """Map TOML's [backup] dir to backup_dir, "" -> None."""
        if isinstance(data, dict):
            backup = data.get("backup")
            if isinstance(backup, dict):
                data = {**data, "backup_dir": backup.get("dir") or None}
        return data


@dataclass(frozen=True)
class Paths:
    private_dir: Path
    db: Path  # <private>/trampo.db
    resume_json: Path  # <private>/resume.json
    profile_toml: Path  # <private>/profile.toml
    resumes_dir: Path  # <private>/resumes
    logs_dir: Path  # <private>/logs


def private_paths(env: Mapping[str, str] = os.environ) -> Paths:
    private_dir = Path(env.get("TRAMPO_PRIVATE_DIR", "private"))
    return Paths(
        private_dir=private_dir,
        db=private_dir / "trampo.db",
        resume_json=private_dir / "resume.json",
        profile_toml=private_dir / "profile.toml",
        resumes_dir=private_dir / "resumes",
        logs_dir=private_dir / "logs",
    )


def load_config(path: Path = Path("config.toml")) -> Config:
    with path.open("rb") as f:
        return Config.model_validate(tomllib.load(f))


def load_profile(path: Path) -> Profile:
    with path.open("rb") as f:
        return Profile.model_validate(tomllib.load(f))
