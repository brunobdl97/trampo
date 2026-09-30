"""Greenhouse client tests: parsing against a recorded fixture, no network."""

import json
from datetime import UTC
from pathlib import Path

import httpx2
import pytest

from trampo.ats import BoardNotFound, client_for
from trampo.ats.greenhouse import GreenhouseClient

FIXTURE = json.loads(Path("tests/fixtures/greenhouse/jobs.json").read_text())


def _handler(request: httpx2.Request) -> httpx2.Response:
    assert request.url.path == "/v1/boards/gitlab/jobs"
    assert request.url.params.get("content") == "true"
    return httpx2.Response(200, json=FIXTURE)


def _client() -> GreenhouseClient:
    return GreenhouseClient(httpx2.Client(transport=httpx2.MockTransport(_handler)))


def test_parses_postings() -> None:
    postings = _client().fetch_postings("gitlab", "GitLab")

    assert len(postings) == 3
    assert {p.posting_id for p in postings} == {"8644569002", "8626740002", "8586667002"}
    assert all(isinstance(p.posting_id, str) for p in postings)

    first = next(p for p in postings if p.posting_id == "8644569002")
    assert first.ats == "greenhouse"
    assert first.board_slug == "gitlab"
    assert first.company == "GitLab"
    assert first.url == "https://job-boards.greenhouse.io/gitlab/jobs/8644569002"
    assert first.location == "Remote, Canada; Remote, United States"
    assert first.workplace is None
    assert first.salary is None


def test_description_is_plain_text() -> None:
    postings = _client().fetch_postings("gitlab", "GitLab")

    for posting in postings:
        assert "<" not in posting.description
        assert ">" not in posting.description
        assert "&amp;" not in posting.description
        assert "&nbsp;" not in posting.description

    # Content is HTML-escaped HTML with a doubly-escaped entity ("&amp;amp;" -> "&amp;"
    # -> "&"); confirms html.unescape + html_to_text fully decode it.
    backend = next(p for p in postings if p.posting_id == "8644569002")
    assert "Required Experience & Skills" in backend.description


def test_published_at_is_utc_or_none() -> None:
    postings = {p.posting_id: p for p in _client().fetch_postings("gitlab", "GitLab")}

    published = postings["8644569002"].published_at
    assert published is not None
    assert published.tzinfo == UTC
    assert published.isoformat() == "2026-08-03T14:38:08+00:00"  # source: -04:00

    # Fixture: 8586667002 has no "first_published" key (see task-5-report.md).
    assert postings["8586667002"].published_at is None


def test_404_raises_board_not_found() -> None:
    def not_found(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404)

    client = GreenhouseClient(httpx2.Client(transport=httpx2.MockTransport(not_found)))
    with pytest.raises(BoardNotFound):
        client.fetch_postings("nonexistent", "Nobody")


def test_client_for_returns_greenhouse() -> None:
    client = client_for("greenhouse", httpx2.Client())
    assert isinstance(client, GreenhouseClient)
    assert client.ats == "greenhouse"
