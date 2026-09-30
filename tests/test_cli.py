import json
import logging
import shutil
from pathlib import Path

import httpx2
import pytest

from trampo import cli
from trampo.cli import main


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
