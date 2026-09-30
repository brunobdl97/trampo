"""discovery.py tests: MockTransport only, no network. The Anthropic response
fixtures under tests/fixtures/anthropic/discovery_*.json are hand-built from
the documented web_search_tool_result shape (Anthropic web-search-tool docs,
fetched 2026-09-30) — there is no API key yet to record them against (Task 20)."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import anthropic
import httpx2
import pytest

from trampo.ats import BoardNotFound
from trampo.claude import make_client
from trampo.config import Config, Track
from trampo.discovery import board_refs_from_urls, discover, discovery_queries
from trampo.models import Ats, BoardRef, Posting
from trampo.store import Store

FIXTURES = Path("tests/fixtures/anthropic")
TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class _FakeAtsClient:
    """A test double for AtsClient — avoids double-mocking a real ATS client
    over httpx2 just to exercise discover()'s validation branch."""

    def __init__(self, ats: Ats, *, raises: type[Exception] | None = None) -> None:
        self.ats: Ats = ats
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    def fetch_postings(self, slug: str, company: str) -> list[Posting]:
        self.calls.append((slug, company))
        if self._raises is not None:
            raise self._raises(slug)
        return []


def _client(handler) -> anthropic.Anthropic:
    return make_client(httpx2.Client(transport=httpx2.MockTransport(handler)))


def _config(*, searches_per_day: int = 10, tracks: list[Track] | None = None) -> Config:
    return Config(
        max_age_days=7,
        repost_days=30,
        discovery_max_searches_per_day=searches_per_day,
        auto_resume_min_fit_score=8,
        ignore_title_keywords=[],
        tracks=tracks
        if tracks is not None
        else [
            Track(id="backend", name="Backend", title_keywords=["backend", "golang"], emphasis="Go")
        ],
    )


def test_board_refs_from_urls() -> None:
    urls = [
        "https://jobs.ashbyhq.com/acme-corp/1234",
        "https://job-boards.greenhouse.io/gitlab/jobs/8644569002",
        "https://boards.greenhouse.io/other-co/jobs/1",
        "https://jobs.lever.co/foo-bar/abcd-1234?utm_source=x#frag",
        "https://jobs.lever.co/foo-bar/other-id",  # same slug as above -> collapses in the set
        "https://boards.greenhouse.io/embed/job_board?for=gitlab",  # non-slug segment -> skipped
        "https://jobs.ashbyhq.com/",  # empty path -> skipped
        "https://example.com/acme-corp/1234",  # other host -> ignored
        "not a url",
    ]

    refs = board_refs_from_urls(urls)

    assert refs == {
        BoardRef(ats="ashby", slug="acme-corp"),
        BoardRef(ats="greenhouse", slug="gitlab"),
        BoardRef(ats="greenhouse", slug="other-co"),
        BoardRef(ats="lever", slug="foo-bar"),
    }


def test_discovery_queries_deterministic_and_no_duplicates() -> None:
    # "golang" appears in both tracks: for a given domain+term the naive product
    # would repeat 'site:<domain> "golang" <term>' twice — it must collapse to one.
    config = _config(
        tracks=[
            Track(
                id="backend", name="Backend", title_keywords=["backend", "golang"], emphasis="Go"
            ),
            Track(
                id="agents",
                name="Agents",
                title_keywords=["golang", "agent engineer"],
                emphasis="Agents",
            ),
        ]
    )

    first = discovery_queries(config)
    second = discovery_queries(config)

    assert first == second  # deterministic
    assert len(first) == len(set(first))  # no duplicates
    assert len(first) == 3 * 3 * 3  # 3 domains x 3 remote terms x 3 unique keywords
    # the domain varies fastest, so the first days' searches cover every ATS
    assert first[:3] == [
        'site:jobs.ashbyhq.com "backend" remote',
        'site:job-boards.greenhouse.io "backend" remote',
        'site:jobs.lever.co "backend" remote',
    ]
    # the bare "boards.greenhouse.io" host is never queried, only "job-boards.greenhouse.io"
    assert all(not q.startswith('site:boards.greenhouse.io "') for q in first)


def test_respects_daily_quota(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = _config(searches_per_day=10)
    store = Store(tmp_path / "trampo.db")
    for i in range(8):
        store.log_discovery_search(TODAY, f"used-query-{i}")

    fixture = json.loads((FIXTURES / "discovery_empty.json").read_text())
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(200, json=fixture)

    added = discover(_client(handler), store, config, {}, TODAY, "claude-opus-5")
    store.close()

    assert len(calls) == 2
    assert added == []


def test_invalid_board_not_added(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = _config(searches_per_day=1)
    store = Store(tmp_path / "trampo.db")
    fixture = json.loads((FIXTURES / "discovery_new_board.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=fixture)

    lever = _FakeAtsClient("lever", raises=BoardNotFound)
    added = discover(_client(handler), store, config, {"lever": lever}, TODAY, "claude-opus-5")
    boards = store.active_boards()
    store.close()

    assert added == []
    assert boards == []
    assert lever.calls == [("acme-corp", "Acme Corp")]


def test_new_board_validated_and_added(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = _config(searches_per_day=1)
    store = Store(tmp_path / "trampo.db")
    fixture = json.loads((FIXTURES / "discovery_new_board.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=fixture)

    lever = _FakeAtsClient("lever")  # does not raise -> validation succeeds
    added = discover(_client(handler), store, config, {"lever": lever}, TODAY, "claude-opus-5")
    boards = store.active_boards()
    searched_today = store.discovery_searches_on(TODAY)
    store.close()

    expected = BoardRef(ats="lever", slug="acme-corp")
    assert added == [expected]
    assert boards == [(expected, "Acme Corp")]
    assert searched_today == 1


def test_known_board_not_duplicated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = _config(searches_per_day=1)
    store = Store(tmp_path / "trampo.db")
    known_ref = BoardRef(ats="greenhouse", slug="acme-corp")
    store.add_board(known_ref, "Acme Corp", NOW)
    fixture = json.loads((FIXTURES / "discovery_known_board.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=fixture)

    greenhouse = _FakeAtsClient("greenhouse")
    added = discover(
        _client(handler), store, config, {"greenhouse": greenhouse}, TODAY, "claude-opus-5"
    )
    boards = store.active_boards()
    store.close()

    assert added == []
    assert boards == [(known_ref, "Acme Corp")]
    assert greenhouse.calls == []  # a known board is never re-validated


def test_inactive_board_found_again_is_reactivated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = _config(searches_per_day=1)
    store = Store(tmp_path / "trampo.db")
    ref = BoardRef(ats="lever", slug="acme-corp")
    store.add_board(ref, "Acme Corp", NOW)
    store.deactivate_board(ref)  # one 404 on a past Run
    fixture = json.loads((FIXTURES / "discovery_new_board.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=fixture)

    lever = _FakeAtsClient("lever")  # validates again
    added = discover(_client(handler), store, config, {"lever": lever}, TODAY, "claude-opus-5")
    boards = store.active_boards()
    store.close()

    assert added == [ref]
    assert boards == [(ref, "Acme Corp")]
    assert lever.calls == [("acme-corp", "Acme Corp")]


def test_unexpected_validation_error_skips_only_that_board(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = _config(searches_per_day=2)
    store = Store(tmp_path / "trampo.db")
    fixture = json.loads((FIXTURES / "discovery_new_board.json").read_text())
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(200, json=fixture)

    lever = _FakeAtsClient("lever", raises=ValueError)  # an odd, unparseable response
    added = discover(_client(handler), store, config, {"lever": lever}, TODAY, "claude-opus-5")
    boards = store.active_boards()
    store.close()

    assert added == []
    assert boards == []
    assert len(calls) == 2  # the day's other search still ran


def test_web_search_error_result_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config = _config(searches_per_day=1)
    store = Store(tmp_path / "trampo.db")
    fixture = json.loads((FIXTURES / "discovery_error.json").read_text())

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=fixture)

    added = discover(_client(handler), store, config, {}, TODAY, "claude-opus-5")
    searched_today = store.discovery_searches_on(TODAY)
    store.close()

    assert added == []
    assert searched_today == 1  # logged even though its result block was an error
