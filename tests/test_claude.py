"""claude.py tests: MockTransport only, no network. The two spend-limit
fixtures are built from the documented error shapes (Anthropic rate-limits
docs, fetched 2026-09-30) — there is no API key yet to record them against
(Task 20)."""

import hashlib
import json
from pathlib import Path

import anthropic
import httpx2
import pytest
from anthropic.types import Message

import trampo.claude as claude_module
from trampo.claude import (
    SpendLimitReached,
    batch_results,
    create_message,
    load_prompt,
    make_client,
    newest_opus,
    submit_batch,
)

FIXTURES = Path("tests/fixtures/anthropic")
_PARAMS = {
    "model": "claude-opus-5",
    "max_tokens": 16000,
    "messages": [{"role": "user", "content": "hi"}],
}


def _client(handler) -> anthropic.Anthropic:
    return make_client(httpx2.Client(transport=httpx2.MockTransport(handler)))


def test_newest_opus_picks_latest_created(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    fixture = json.loads((FIXTURES / "models_list.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/models"
        return httpx2.Response(200, json=fixture)

    assert newest_opus(_client(handler)) == "claude-opus-5-20260201"


def test_prompt_hash_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = "You are Trampo's judge.\n"
    (tmp_path / "greeting.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(claude_module, "PROMPTS", tmp_path)

    first = load_prompt("greeting")
    second = load_prompt("greeting")

    assert first == second
    assert len(first.hash) == 12
    assert first.hash == hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def test_batch_results_none_while_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    fixture = json.loads((FIXTURES / "batch_in_progress.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages/batches/msgbatch_test123"
        return httpx2.Response(200, json=fixture)

    assert batch_results(_client(handler), "msgbatch_test123") is None


def test_batch_results_keyed_by_custom_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    ended = json.loads((FIXTURES / "batch_ended.json").read_text())
    jsonl = (FIXTURES / "batch_results.jsonl").read_bytes()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/messages/batches/msgbatch_test123/results":
            return httpx2.Response(200, content=jsonl)
        if request.url.path == "/v1/messages/batches/msgbatch_test123":
            return httpx2.Response(200, json=ended)
        raise AssertionError(request.url.path)

    results = batch_results(_client(handler), "msgbatch_test123")

    assert results is not None
    assert results.keys() == {"job-1-succeeded", "job-2-errored", "job-3-canceled", "job-4-expired"}

    succeeded = results["job-1-succeeded"]
    assert isinstance(succeeded, Message)
    block = succeeded.content[0]
    assert block.type == "text"
    assert block.text == "Fit score: 8"

    assert results["job-2-errored"] == "invalid_request_error: max_tokens: 999999999 is too large"
    assert results["job-3-canceled"] == "canceled"
    assert results["job-4-expired"] == "expired"


def test_spend_limit_maps_to_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """400 workspace-limit error, via submit_batch."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    fixture = json.loads((FIXTURES / "spend_limit_400.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages/batches"
        return httpx2.Response(400, json=fixture)

    with pytest.raises(SpendLimitReached) as exc_info:
        submit_batch(_client(handler), {"job-1": _PARAMS})

    assert str(exc_info.value) == fixture["error"]["message"]
    assert isinstance(exc_info.value.__cause__, anthropic.BadRequestError)


def test_spend_limit_maps_to_exception_tier_cap_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 tier-cap error, via submit_batch — the next Run's batch submission
    is where Ruling R4 says this surfaces."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    fixture = json.loads((FIXTURES / "spend_limit_429.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages/batches"
        return httpx2.Response(429, json=fixture)

    # max_retries=0: the SDK auto-retries 429s with a real backoff sleep otherwise.
    client = _client(handler).with_options(max_retries=0)

    with pytest.raises(SpendLimitReached) as exc_info:
        submit_batch(client, {"job-1": _PARAMS})

    assert str(exc_info.value) == fixture["error"]["message"]
    assert isinstance(exc_info.value.__cause__, anthropic.RateLimitError)


def test_create_message_maps_spend_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_message (Discovery, on-demand Tailored resumes, eval) goes
    through the same spend-limit wrapper as the batch calls."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    fixture = json.loads((FIXTURES / "spend_limit_400.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages"
        return httpx2.Response(400, json=fixture)

    with pytest.raises(SpendLimitReached) as exc_info:
        create_message(_client(handler), _PARAMS)

    assert str(exc_info.value) == fixture["error"]["message"]
