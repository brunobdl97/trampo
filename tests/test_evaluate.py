"""evaluate.py tests: re-judging Overrides and the `trampo eval` CLI's
cost-confirmation gate. No network — Anthropic calls go through
httpx2.MockTransport, Message responses built like tests/test_judge.py's,
and the CLI harness follows tests/test_cli.py's style."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import httpx2
import pytest

from trampo import cli
from trampo.claude import make_client
from trampo.cli import main
from trampo.config import load_config, load_profile
from trampo.evaluate import EvalReport, evaluate
from trampo.models import Posting, VerdictValue
from trampo.resume.model import load_resume
from trampo.store import Store

CONFIG = load_config()
PROFILE = load_profile(Path("private.example/profile.toml"))
RESUME = load_resume(Path("private.example/resume.json"))
MODEL = "claude-opus-5"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _store_with_overrides(
    tmp_path: Path, overrides: dict[str, VerdictValue]
) -> tuple[Store, dict[str, int]]:
    """A Store with one overridden Job per (title -> to_verdict) entry."""
    store = Store(tmp_path / "trampo.db")
    run_id = store.start_run(NOW)
    job_ids: dict[str, int] = {}
    for title, to_verdict in overrides.items():
        job_id = store.create_job("Acme", title, title.lower(), run_id, NOW)
        store.upsert_posting(
            Posting(
                ats="greenhouse",
                board_slug="acme",
                posting_id=title,
                company="Acme",
                title=title,
                location="Remote - Brazil",
                url=f"https://acme.example/{title}",
                description="Build things.",
                workplace="remote",
                salary=None,
                published_at=NOW,
            ),
            job_id,
            NOW,
        )
        store.override_verdict(job_id, to_verdict, "candidate override", NOW)
        job_ids[title] = job_id
    return store, job_ids


def _message(content: list[dict], stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_01abc",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-20260201",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def _judgment(verdict: str) -> dict:
    return {
        "track": "backend",
        "verdict": verdict,
        "reason": "Avaliação.",
        "remote": True,
        "open_to_brazil": True,
        "requires_us_work_authorization": False,
        "fit_score": 7,
        "fit_reason": "Aderente.",
        "job_language": "en",
    }


def _client_for(outcomes: dict[str, dict | None | str]) -> anthropic.Anthropic:
    """outcomes maps Job title -> a Judgment payload dict, None for a refusal,
    or "invalid" for a response with no text block (ValueError). The Job
    title is read back out of the request body, so response order never has
    to match store.overridden_jobs()'s order."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        user_text = body["messages"][0]["content"]
        title = next(t for t in outcomes if f"Title: {t}" in user_text)
        outcome = outcomes[title]
        if outcome is None:
            return httpx2.Response(200, json=_message(content=[], stop_reason="refusal"))
        if outcome == "invalid":
            return httpx2.Response(200, json=_message(content=[], stop_reason="max_tokens"))
        return httpx2.Response(
            200, json=_message(content=[{"type": "text", "text": json.dumps(outcome)}])
        )

    return make_client(httpx2.Client(transport=httpx2.MockTransport(handler)))


def test_agreement_rate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    store, _ = _store_with_overrides(tmp_path, {"Job 0": "eligible", "Job 1": "rejected"})
    client = _client_for(
        {
            "Job 0": _judgment("eligible"),  # agrees
            "Job 1": _judgment("needs_review"),  # disagrees
        }
    )

    report = evaluate(client, store, CONFIG, PROFILE, RESUME, MODEL)
    store.close()

    assert report.total == 2
    assert report.agree == 1


def test_confusion_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    store, job_ids = _store_with_overrides(
        tmp_path,
        {"Job 0": "eligible", "Job 1": "eligible", "Job 2": "rejected"},
    )
    client = _client_for(
        {
            "Job 0": _judgment("eligible"),
            "Job 1": _judgment("needs_review"),
            "Job 2": "invalid",
        }
    )

    report = evaluate(client, store, CONFIG, PROFILE, RESUME, MODEL)
    store.close()

    assert report.confusion[("eligible", "eligible")] == 1
    assert report.confusion[("eligible", "needs_review")] == 1
    assert report.confusion[("rejected", "error")] == 1
    assert sorted(report.disagreements) == sorted(
        [
            (job_ids["Job 1"], "eligible", "needs_review"),
            (job_ids["Job 2"], "rejected", "error"),
        ]
    )


def test_no_overrides_is_empty_report(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no call expected: no overridden Jobs")

    client = make_client(httpx2.Client(transport=httpx2.MockTransport(handler)))

    report = evaluate(client, store, CONFIG, PROFILE, RESUME, MODEL)
    store.close()

    assert report == EvalReport(total=0, agree=0, confusion={}, disagreements=[])


def test_refusal_counts_as_needs_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    store, _ = _store_with_overrides(tmp_path, {"Job 0": "needs_review"})
    client = _client_for({"Job 0": None})

    report = evaluate(client, store, CONFIG, PROFILE, RESUME, MODEL)
    store.close()

    assert report.agree == 1
    assert report.confusion[("needs_review", "needs_review")] == 1


def test_value_error_counts_as_error_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    store, job_ids = _store_with_overrides(tmp_path, {"Job 0": "eligible"})
    client = _client_for({"Job 0": "invalid"})

    report = evaluate(client, store, CONFIG, PROFILE, RESUME, MODEL)
    store.close()

    assert report.agree == 0
    assert report.disagreements == [(job_ids["Job 0"], "eligible", "error")]
    assert report.confusion[("eligible", "error")] == 1


def test_cli_requires_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert main(["eval"]) == 2

    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_zero_overrides_makes_no_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    private_dir = tmp_path / "private"
    shutil.copytree("private.example", private_dir)
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(private_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no call expected: zero overrides")

    monkeypatch.setattr(
        cli,
        "make_client",
        lambda: make_client(httpx2.Client(transport=httpx2.MockTransport(handler))),
    )

    assert main(["eval"]) == 0

    assert "Overrides" in capsys.readouterr().out


def test_cli_asks_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    private_dir = tmp_path / "private"
    shutil.copytree("private.example", private_dir)
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(private_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    store, _ = _store_with_overrides(private_dir, {"Job 0": "eligible"})
    store.close()

    called = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        raise AssertionError("no request should reach the transport")

    monkeypatch.setattr(
        cli,
        "make_client",
        lambda: make_client(httpx2.Client(transport=httpx2.MockTransport(handler))),
    )
    monkeypatch.setattr("builtins.input", lambda _: "n")

    assert main(["eval"]) == 0
    assert not called

    out = capsys.readouterr().out
    assert "1" in out  # 1 API call announced


def test_cli_eof_on_confirmation_declines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """stdin closed/exhausted (cron, CI, `< /dev/null`, no --yes): input() raises
    EOFError, which must be treated as "no", not crash the CLI."""
    private_dir = tmp_path / "private"
    shutil.copytree("private.example", private_dir)
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(private_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    store, _ = _store_with_overrides(private_dir, {"Job 0": "eligible"})
    store.close()

    called = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        raise AssertionError("no request should reach the transport")

    monkeypatch.setattr(
        cli,
        "make_client",
        lambda: make_client(httpx2.Client(transport=httpx2.MockTransport(handler))),
    )

    def _raise_eof(_: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise_eof)

    assert main(["eval"]) == 0
    assert not called
    assert capsys.readouterr().out.endswith("\n")
