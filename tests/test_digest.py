"""digest.py tests: MockTransport only, no network. The fixture
tests/fixtures/telegram/sendMessage-ok.json is hand-built from the
documented sendMessage response shape (Telegram Bot API docs, fetched
2026-09-30) — there is no bot token yet (Task 20), so nothing was recorded
against a real send."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from trampo.digest import (
    TELEGRAM_LIMIT,
    Telegram,
    TelegramError,
    format_digest,
    format_failure,
    format_spend_cap_alert,
)
from trampo.models import JobRow

FIXTURE = json.loads(Path("tests/fixtures/telegram/sendMessage-ok.json").read_text())


def _job(
    id: int = 1,
    company: str = "Acme",
    title: str = "Backend Engineer",
    track: str = "backend",
    fit_score: int | None = 8,
    url: str = "https://boards.example.com/acme/jobs/1",
) -> JobRow:
    return JobRow(
        id=id,
        company=company,
        title=title,
        normalized_title=title.lower(),
        created_run_id=1,
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
        closed_at=None,
        verdict="eligible",
        verdict_reason=None,
        track=track,  # type: ignore[arg-type]
        fit_score=fit_score,
        fit_reason=None,
        job_language="en",
        status="new",
        notes="",
        resume_path=None,
        locations=["Remote"],
        urls=[url],
    )


def test_escapes_html() -> None:
    job = _job(title="C++ & Go <Senior>", company="A & B Inc")

    [message] = format_digest([job], needs_review=0, rejected=0)

    assert "C++ &amp; Go &lt;Senior&gt;" in message
    assert "A &amp; B Inc" in message
    assert "<Senior>" not in message
    assert "<Senior" not in message.replace("&lt;Senior&gt;", "")


def test_splits_long_digest() -> None:
    jobs = [
        _job(id=i, title=f"Engenheiro de Software Sênior Nível {i} - Plataforma de Pagamentos")
        for i in range(80)
    ]

    messages = format_digest(jobs, needs_review=3, rejected=5)

    assert len(messages) > 1
    for message in messages:
        assert len(message) <= TELEGRAM_LIMIT

    # No Job line lost, duplicated or split: exactly one bullet line per Job.
    assert sum(message.count("• <b>") for message in messages) == 80

    # The counts line is on the last message only.
    assert "3" in messages[-1] and "5" in messages[-1]
    for message in messages[:-1]:
        assert "para revisar" not in message


def test_counts_line() -> None:
    job = _job()

    messages = format_digest([job], needs_review=2, rejected=1)

    assert len(messages) == 1
    last_line = messages[-1].splitlines()[-1]
    assert "2" in last_line
    assert "1" in last_line
    assert "revisar" in last_line.lower()
    assert "rejeitada" in last_line.lower()


def test_send_posts_expected_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx2.Response(200, json=FIXTURE)

    telegram = Telegram(
        httpx2.Client(transport=httpx2.MockTransport(handler)), token="123:ABC", chat_id="42"
    )
    telegram.send("<b>hi</b>")

    assert captured["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert captured["body"]["chat_id"] == "42"
    assert captured["body"]["parse_mode"] == "HTML"
    assert captured["body"]["text"] == "<b>hi</b>"
    assert captured["body"]["link_preview_options"] == {"is_disabled": True}


def test_send_raises_on_not_ok_response() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
        )

    telegram = Telegram(
        httpx2.Client(transport=httpx2.MockTransport(handler)), token="123:ABC", chat_id="42"
    )

    with pytest.raises(TelegramError, match="chat not found"):
        telegram.send("hi")


def test_send_error_never_leaks_the_bot_token() -> None:
    # The underlying transport error's own message embeds the full request
    # URL (as real connection errors typically do) — a naive `str(exc)`/
    # `repr(exc)` in the implementation would leak the token here.
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"Connection refused: {request.url}")

    token = "123456:super-secret-token"
    telegram = Telegram(
        httpx2.Client(transport=httpx2.MockTransport(handler)), token=token, chat_id="42"
    )

    with pytest.raises(TelegramError) as exc_info:
        telegram.send("hi")

    assert token not in str(exc_info.value)


def test_send_raises_telegram_error_on_non_json_error_body() -> None:
    """A 502 from an intermediary (e.g. a proxy) returns an HTML body, not
    Telegram's JSON — must still raise TelegramError, not json.JSONDecodeError."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(502, content=b"<html><body>Bad Gateway</body></html>")

    telegram = Telegram(
        httpx2.Client(transport=httpx2.MockTransport(handler)), token="123:ABC", chat_id="42"
    )

    with pytest.raises(TelegramError, match="502"):
        telegram.send("hi")


def test_format_failure_fits_limit_and_escapes_html() -> None:
    message = format_failure("Traceback: <boom> & things went wrong " * 200)

    assert len(message) <= TELEGRAM_LIMIT
    assert "<boom>" not in message
    assert "&lt;boom&gt;" in message


def test_format_spend_cap_alert_fits_limit_and_escapes_html() -> None:
    message = format_spend_cap_alert(7)

    assert len(message) <= TELEGRAM_LIMIT
    assert "7" in message
