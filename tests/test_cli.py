import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from trampo import cli
from trampo.claude import make_client
from trampo.cli import main
from trampo.models import Posting
from trampo.store import Store

FIXTURES = Path("tests/fixtures")
BASE_RESUME = json.loads(Path("private.example/resume.json").read_text())


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == "trampo 0.1.0"


def test_run_without_credentials_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-bot-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert main(["run"]) == 2

    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err
    assert "TELEGRAM_CHAT_ID" in err
    assert "TELEGRAM_BOT_TOKEN" not in err  # set -> not reported
    assert "secret-bot-token" not in err  # never a value
    assert not (tmp_path / "private").exists()  # nothing built: no logs dir, no DB


def test_resume_base_writes_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private_dir = tmp_path / "private"
    shutil.copytree("private.example", private_dir)
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(private_dir))

    assert main(["resume", "--base"]) == 0

    assert (private_dir / "resumes" / "base-en.pdf").read_bytes().startswith(b"%PDF")


def test_resume_without_base_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(tmp_path / "private"))

    assert main(["resume"]) == 2
    assert "--base" in capsys.readouterr().err


def test_run_setup_failure_alerts_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    shutil.copytree("private.example", tmp_path / "private")
    (tmp_path / "config.toml").write_text("max_age_days = \n")  # a config typo
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent: list[str] = []

    def telegram(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/bottest-token/sendMessage"
        sent.append(json.loads(request.content)["text"])
        return httpx2.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(
        cli, "_http", lambda: httpx2.Client(transport=httpx2.MockTransport(telegram))
    )

    assert main(["run"]) == 1

    [alert] = sent
    assert alert.startswith("<b>Trampo: a execução falhou</b>")
    assert "TOMLDecodeError" in alert
    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)


def _private_with_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tailored: dict[str, Any] | None
) -> tuple[Path, int]:
    """A private dir with one Job, and a fake Anthropic API whose regular call
    answers with `tailored` as the Tailored resume (None: the model refuses)."""
    private_dir = tmp_path / "private"
    shutil.copytree("private.example", private_dir)
    monkeypatch.setenv("TRAMPO_PRIVATE_DIR", str(private_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    store = Store(private_dir / "trampo.db")
    title = "Senior Backend Engineer"
    job_id = store.create_job("Acme", title, title.lower(), store.start_run(now), now)
    posting = Posting(
        ats="greenhouse",
        board_slug="acme",
        posting_id="1",
        company="Acme",
        title=title,
        location="Remote - Brazil",
        url="https://job-boards.greenhouse.io/acme/jobs/1",
        description="Build our payments platform in Go.",
        workplace="remote",
        salary=None,
        published_at=now,
    )
    store.upsert_posting(posting, job_id, now)
    store.close()

    def claude(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/models":
            models = json.loads((FIXTURES / "anthropic/models_list.json").read_text())
            return httpx2.Response(200, json=models)
        assert request.url.path == "/v1/messages"
        message = {
            "id": "msg_01abc",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-20260201",
            "content": [] if tailored is None else [{"type": "text", "text": json.dumps(tailored)}],
            "stop_reason": "refusal" if tailored is None else "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
        return httpx2.Response(200, json=message)

    fake = httpx2.Client(transport=httpx2.MockTransport(claude))
    monkeypatch.setattr(cli, "make_client", lambda: make_client(fake))
    return private_dir, job_id


def test_resume_job_writes_tailored_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    private_dir, job_id = _private_with_job(tmp_path, monkeypatch, BASE_RESUME)

    assert main(["resume", str(job_id)]) == 0

    out = Path(capsys.readouterr().out.strip())
    assert out == private_dir / "resumes" / "acme-senior-backend-engineer.pdf"
    assert out.read_bytes().startswith(b"%PDF")


def test_resume_job_refused_by_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    invented = json.loads(json.dumps(BASE_RESUME))
    invented["work"][0]["name"] = "Globex"
    invented["work"][1]["highlights"][0] = "Built an API handling 75k requests/day"
    private_dir, job_id = _private_with_job(tmp_path, monkeypatch, invented)

    assert main(["resume", str(job_id)]) == 1

    captured = capsys.readouterr()
    assert "Globex" in captured.err
    assert "75" in captured.err
    assert captured.out == ""
    assert not (private_dir / "resumes").exists()


def test_resume_unknown_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, job_id = _private_with_job(tmp_path, monkeypatch, BASE_RESUME)

    assert main(["resume", str(job_id + 1)]) == 1

    captured = capsys.readouterr()
    assert f"Job {job_id + 1} not found" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_resume_job_refused_by_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    private_dir, job_id = _private_with_job(tmp_path, monkeypatch, None)

    assert main(["resume", str(job_id)]) == 1

    captured = capsys.readouterr()
    assert "refused" in captured.err
    assert captured.out == ""
    assert not (private_dir / "resumes").exists()
