"""Base resume model: a JSON Resume (https://jsonresume.org/schema) subset.

Field names follow the JSON Resume schema verbatim (camelCase) since that
schema is the contract with `private/resume.json` and Claude's structured
output for Tailored resumes.
"""

import re
from datetime import date
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel

# Shape check first (rejects "2024-1" and "12/2024", which strptime/fromisoformat
# alone would not), then a calendar check (rejects "2024-13" and "2024-02-30").
_DATE_RE = re.compile(r"^\d{4}-(\d{2})(-\d{2})?$")


def _check_date(value: str) -> str:
    match = _DATE_RE.match(value)
    if not match:
        raise ValueError(f"invalid date {value!r}; expected YYYY-MM or YYYY-MM-DD")
    month, day = match.groups()
    if not 1 <= int(month) <= 12:
        raise ValueError(f"invalid date {value!r}: month {month} out of range")
    if day is not None:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid date {value!r}: {exc}") from exc
    return value


Date = Annotated[str, AfterValidator(_check_date)]


class Location(BaseModel):
    city: str | None = None
    countryCode: str | None = None


class SocialProfile(BaseModel):
    """A JSON Resume "profiles" entry (LinkedIn, GitHub); not config.Profile."""

    network: str
    url: str


class Basics(BaseModel):
    name: str
    label: str
    email: str
    phone: str | None = None
    summary: str
    location: Location | None = None
    profiles: list[SocialProfile] = []


class Work(BaseModel):
    name: str
    position: str
    startDate: Date
    endDate: Date | None = None  # None: current job
    summary: str | None = None
    highlights: list[str] = []


class Education(BaseModel):
    institution: str
    area: str
    studyType: str
    startDate: Date | None = None
    endDate: Date | None = None


class Skill(BaseModel):
    name: str
    keywords: list[str] = []


class Language(BaseModel):
    language: str
    fluency: str


class Project(BaseModel):
    name: str
    description: str
    highlights: list[str] = []
    url: str | None = None


class Resume(BaseModel):
    basics: Basics
    work: list[Work]
    education: list[Education] = []
    skills: list[Skill] = []
    languages: list[Language] = []
    projects: list[Project] = []


def load_resume(path: Path) -> Resume:
    return Resume.model_validate_json(path.read_bytes())
