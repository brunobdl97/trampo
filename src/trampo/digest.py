"""Digest formatting (pt-BR, Telegram HTML parse mode) and a small Telegram
Bot API client to send it."""

import html
from typing import Any

import httpx2

from trampo.config import TrackId
from trampo.models import JobRow

TELEGRAM_LIMIT = 4096

_TRACK_LABELS: dict[TrackId, str] = {"backend": "Backend", "agents": "Agentes/Automação"}

_TELEGRAM_API = "https://api.telegram.org"


class TelegramError(Exception):
    """A Telegram Bot API call failed. Never carries the request URL — it
    contains the bot token, and this text ends up in logs and failure alerts."""


def _fit_escaped(raw: str, available: int) -> str:
    """Truncate `raw` so html.escape(raw, quote=True) fits in `available`
    chars, without cutting in the middle of an escaped entity."""
    text = raw[:available]
    while text:
        escaped = html.escape(text, quote=True)
        if len(escaped) <= available:
            return escaped
        text = text[:-1]
    return ""


def _job_line(job: JobRow) -> str:
    company = html.escape(job.company, quote=True)
    url = html.escape(job.urls[0], quote=True)
    track_label = _TRACK_LABELS[job.track] if job.track is not None else "—"
    fit = job.fit_score if job.fit_score is not None else "–"
    title = html.escape(job.title, quote=True)
    line = f'• <b>{company}</b> — <a href="{url}">{title}</a> · {track_label} · Fit {fit}/10'
    if len(line) <= TELEGRAM_LIMIT:
        return line
    # ponytail: a single Job line this long can't happen in practice (rulings say so);
    # shrink the title to fit rather than build a wrapping scheme nothing else needs.
    fixed_len = len(line) - len(title)
    title = _fit_escaped(job.title, TELEGRAM_LIMIT - fixed_len)
    return f'• <b>{company}</b> — <a href="{url}">{title}</a> · {track_label} · Fit {fit}/10'


def _pack_lines(lines: list[str]) -> list[str]:
    """Greedy whole-line packing: each returned message is `<= TELEGRAM_LIMIT`
    chars, lines joined with "\\n", and no line is ever split."""
    messages: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        extra = len(line) if not current else len(line) + 1  # +1 for the joining "\n"
        if current and length + extra > TELEGRAM_LIMIT:
            messages.append("\n".join(current))
            current = []
            length = 0
            extra = len(line)
        current.append(line)
        length += extra
    if current:
        messages.append("\n".join(current))
    return messages


def format_digest(jobs: list[JobRow], needs_review: int, rejected: int) -> list[str]:
    header = f"<b>Trampo: {len(jobs)} vaga(s) nova(s)</b>"
    counts = f"{needs_review} para revisar · {rejected} rejeitada(s)"
    lines = [header, *(_job_line(job) for job in jobs), counts]
    return _pack_lines(lines)


def format_failure(error: str) -> str:
    prefix = "<b>Trampo: a execução falhou</b>\n<code>"
    suffix = "</code>"
    available = TELEGRAM_LIMIT - len(prefix) - len(suffix)
    return f"{prefix}{_fit_escaped(error, available)}{suffix}"


def format_spend_cap_alert(pending: int) -> str:
    return (
        "<b>Trampo: limite de gastos da Anthropic atingido</b>\n"
        f"{pending} vaga(s) seguem pendentes até a renovação do limite."
    )


class Telegram:
    """Minimal Telegram Bot API client (sendMessage only)."""

    def __init__(self, http: httpx2.Client, token: str, chat_id: str) -> None:
        self._http = http
        self._token = token
        self._chat_id = chat_id

    def send(self, text: str) -> None:
        url = f"{_TELEGRAM_API}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        try:
            response = self._http.post(url, json=payload)
        except httpx2.HTTPError as exc:
            # Never include exc's str/repr: httpx2 transport errors embed the
            # request URL, which contains the bot token.
            raise TelegramError(f"Telegram request failed: {type(exc).__name__}") from None

        try:
            body: Any = response.json()
        except ValueError:
            # Non-2xx from an intermediary (proxy, load balancer) can return
            # a non-JSON body (HTML error page); still raise TelegramError.
            body = None

        ok = isinstance(body, dict) and body.get("ok", False)
        if response.status_code >= 400 or not ok:
            description = body.get("description") if isinstance(body, dict) else None
            raise TelegramError(
                f"Telegram API error {response.status_code}: {description or 'unknown error'}"
            )
