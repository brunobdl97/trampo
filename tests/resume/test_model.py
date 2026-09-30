from pathlib import Path

import pytest
from pydantic import ValidationError

from trampo.resume import Resume, load_resume

_MINIMAL_BASICS = {
    "name": "Alex Silva",
    "label": "Backend Engineer",
    "email": "alex.silva@example.com",
    "summary": "Backend engineer.",
}


def test_example_resume_loads() -> None:
    resume = load_resume(Path("private.example/resume.json"))
    assert resume.basics.name == "Alex Silva"
    assert len(resume.work) >= 2


def test_missing_name_fails() -> None:
    basics = {k: v for k, v in _MINIMAL_BASICS.items() if k != "name"}
    with pytest.raises(ValidationError, match="name"):
        Resume.model_validate({"basics": basics, "work": []})


def test_bad_date_fails() -> None:
    work = [{"name": "Acme", "position": "Engineer", "startDate": "12/2024"}]
    with pytest.raises(ValidationError, match="startDate"):
        Resume.model_validate({"basics": _MINIMAL_BASICS, "work": work})


def test_impossible_date_fails() -> None:
    for bad_date in ("2024-13", "2024-02-30"):
        work = [{"name": "Acme", "position": "Engineer", "startDate": bad_date}]
        with pytest.raises(ValidationError, match="startDate"):
            Resume.model_validate({"basics": _MINIMAL_BASICS, "work": work})
