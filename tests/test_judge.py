"""judge.py tests: request shape, cache prefix stability, and response
parsing. No network — parse_judgment is exercised against Message objects
built directly, per Ruling task-11-rulings.md."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from anthropic.types import Message
from pydantic import ValidationError

from trampo.claude import load_prompt
from trampo.config import load_config, load_profile
from trampo.judge import judge_params, parse_judgment
from trampo.models import JobRow, JobWithPostings, Judgment, Posting
from trampo.resume.model import load_resume

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
CONFIG = load_config()
PROFILE = load_profile(Path("private.example/profile.toml"))
RESUME = load_resume(Path("private.example/resume.json"))
PROMPT = load_prompt("judge")
MODEL = "claude-opus-5"


def _posting(**overrides: object) -> Posting:
    defaults: dict[str, object] = {
        "ats": "greenhouse",
        "board_slug": "acme",
        "posting_id": "p1",
        "company": "Acme",
        "title": "Senior Backend Engineer",
        "location": "Remote - Brazil",
        "url": "https://acme.example/p1",
        "description": "Build our payments platform in Go.",
        "workplace": "remote",
        "salary": None,
        "published_at": NOW,
    }
    defaults.update(overrides)
    return Posting.model_validate(defaults)


def _job(**overrides: object) -> JobRow:
    defaults: dict[str, object] = {
        "id": 1,
        "company": "Acme",
        "title": "Senior Backend Engineer",
        "normalized_title": "senior backend engineer",
        "created_run_id": 1,
        "created_at": NOW,
        "closed_at": None,
        "verdict": "pending",
        "verdict_reason": None,
        "track": None,
        "fit_score": None,
        "fit_reason": None,
        "job_language": None,
        "status": "new",
        "notes": "",
        "resume_path": None,
        "locations": ["Remote - Brazil"],
        "urls": ["https://acme.example/p1"],
    }
    defaults.update(overrides)
    return JobRow.model_validate(defaults)


def _item(**overrides: object) -> JobWithPostings:
    job = _job(**overrides)
    return JobWithPostings(job=job, postings=[_posting(company=job.company, title=job.title)])


def _message(*, stop_reason: str = "end_turn", content: list[dict]) -> Message:
    return Message.model_validate(
        {
            "id": "msg_01abc",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-20260201",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    )


def test_params_shape() -> None:
    params = judge_params(_item(), RESUME, CONFIG, PROFILE, PROMPT, MODEL)

    assert params["model"] == MODEL
    assert params["thinking"] == {"type": "adaptive"}

    system = params["system"]
    assert isinstance(system, list)
    assert len(system) == 2
    assert system[0].get("cache_control") is None
    assert system[1]["cache_control"] == {"type": "ephemeral"}

    schema = params["output_config"]["format"]["schema"]
    assert set(schema["properties"]) == set(Judgment.model_fields)


def test_prefix_identical_across_jobs() -> None:
    job_a = _item(id=1, company="Acme", title="Senior Backend Engineer")
    job_b = _item(id=2, company="Globex", title="AI Agent Engineer")

    params_a = judge_params(job_a, RESUME, CONFIG, PROFILE, PROMPT, MODEL)
    params_b = judge_params(job_b, RESUME, CONFIG, PROFILE, PROMPT, MODEL)

    assert params_a["system"] == params_b["system"]


def test_no_placeholder_survives_rendering() -> None:
    params = judge_params(_item(), RESUME, CONFIG, PROFILE, PROMPT, MODEL)
    instructions = params["system"][0]["text"]
    assert "{{" not in instructions


def test_parse_valid_judgment() -> None:
    payload = (
        '{"track": "backend", "verdict": "eligible", "reason": "Atende aos critérios.", '
        '"remote": true, "open_to_brazil": true, "requires_us_work_authorization": false, '
        '"fit_score": 8, "fit_reason": "Boa aderência ao perfil.", "job_language": "en"}'
    )
    message = _message(content=[{"type": "text", "text": payload}])

    judgment = parse_judgment(message)

    assert judgment is not None
    assert judgment.track == "backend"
    assert judgment.verdict == "eligible"
    assert judgment.fit_score == 8


def test_parse_valid_judgment_skips_thinking_block() -> None:
    payload = (
        '{"track": "agents", "verdict": "needs_review", "reason": "Ambíguo.", '
        '"remote": true, "open_to_brazil": null, "requires_us_work_authorization": null, '
        '"fit_score": 5, "fit_reason": "Parcialmente aderente.", "job_language": "pt"}'
    )
    message = _message(
        content=[
            {"type": "thinking", "thinking": "reasoning...", "signature": "sig"},
            {"type": "text", "text": payload},
        ]
    )

    judgment = parse_judgment(message)

    assert judgment is not None
    assert judgment.verdict == "needs_review"


def test_parse_refusal_returns_none() -> None:
    message = _message(stop_reason="refusal", content=[])
    assert parse_judgment(message) is None


def test_fit_score_out_of_range_rejected() -> None:
    payload = (
        '{"track": "backend", "verdict": "eligible", "reason": "x", '
        '"remote": true, "open_to_brazil": true, "requires_us_work_authorization": false, '
        '"fit_score": 11, "fit_reason": "x", "job_language": "en"}'
    )
    message = _message(content=[{"type": "text", "text": payload}])

    with pytest.raises(ValidationError):
        parse_judgment(message)


def test_parse_no_text_block_raises_value_error() -> None:
    """max_tokens can truncate a response before any text block is emitted."""
    message = _message(
        stop_reason="max_tokens",
        content=[{"type": "thinking", "thinking": "still reasoning...", "signature": "sig"}],
    )

    with pytest.raises(ValueError):
        parse_judgment(message)
