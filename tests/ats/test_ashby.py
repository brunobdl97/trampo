"""Ashby client tests: parsing against a recorded fixture, no network."""

import json
from datetime import UTC
from pathlib import Path

import httpx2
import pytest

from trampo.ats import BoardNotFound, client_for
from trampo.ats.ashby import AshbyClient
from trampo.models import Salary

FIXTURE = json.loads(Path("tests/fixtures/ashby/job-board.json").read_text())


def _handler(request: httpx2.Request) -> httpx2.Response:
    assert request.url.path == "/posting-api/job-board/ramp"
    assert request.url.params.get("includeCompensation") == "true"
    return httpx2.Response(200, json=FIXTURE)


def _client() -> AshbyClient:
    return AshbyClient(httpx2.Client(transport=httpx2.MockTransport(_handler)))


def test_parses_listed_postings_only() -> None:
    postings = _client().fetch_postings("ramp", "Ramp")

    # Fixture has 3 jobs; one has isListed: false and must be skipped.
    assert len(postings) == 2
    ids = {p.posting_id for p in postings}
    assert ids == {
        "d204e136-2749-42de-82b4-88a0dd352090",
        "1515fe6d-1d8e-475b-a5ee-cefe43e78cb7",
    }
    assert "7c26780b-7923-42d5-8111-9825ff996966" not in ids  # isListed: false

    engineer = next(p for p in postings if p.posting_id == "d204e136-2749-42de-82b4-88a0dd352090")
    assert engineer.ats == "ashby"
    assert engineer.board_slug == "ramp"
    assert engineer.company == "Ramp"
    assert engineer.title == "Applied AI Engineer"
    assert engineer.url == ("https://jobs.ashbyhq.com/ramp/d204e136-2749-42de-82b4-88a0dd352090")
    # location + secondaryLocations joined with "; ".
    assert engineer.location == "New York, NY (HQ); San Francisco, CA"
    assert "<" not in engineer.description
    assert engineer.description.startswith("ABOUT RAMP")


def test_published_at_parsed_to_utc() -> None:
    postings = {p.posting_id: p for p in _client().fetch_postings("ramp", "Ramp")}

    engineer = postings["d204e136-2749-42de-82b4-88a0dd352090"]
    assert engineer.published_at is not None
    assert engineer.published_at.tzinfo == UTC
    assert engineer.published_at.isoformat() == "2026-01-12T16:46:17.571000+00:00"


def test_workplace_mapping() -> None:
    # Fixture covers Hybrid/OnSite; Remote and the isRemote fallback (missing
    # workplaceType) are synthesized here, as no real posting on the recorded
    # board had them.
    payload = {
        "jobs": [
            {
                "id": "r1",
                "title": "Remote role",
                "jobUrl": "https://jobs.ashbyhq.com/acme/r1",
                "isListed": True,
                "workplaceType": "Remote",
            },
            {
                "id": "f1",
                "title": "Fallback to isRemote",
                "jobUrl": "https://jobs.ashbyhq.com/acme/f1",
                "isListed": True,
                "isRemote": True,
            },
            {
                "id": "n1",
                "title": "Neither workplaceType nor isRemote",
                "jobUrl": "https://jobs.ashbyhq.com/acme/n1",
                "isListed": True,
                "isRemote": False,
            },
            {
                "id": "m1",
                "title": "Missing both keys",
                "jobUrl": "https://jobs.ashbyhq.com/acme/m1",
                "isListed": True,
            },
        ],
        "apiVersion": 1,
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    client = AshbyClient(httpx2.Client(transport=httpx2.MockTransport(handler)))
    postings = {p.posting_id: p for p in client.fetch_postings("acme", "Acme")}

    assert postings["r1"].workplace == "remote"
    assert postings["f1"].workplace == "remote"
    assert postings["n1"].workplace is None  # isRemote false does not mean onsite
    assert postings["m1"].workplace is None


def test_compensation_to_salary() -> None:
    postings = {p.posting_id: p for p in _client().fetch_postings("ramp", "Ramp")}

    engineer = postings["d204e136-2749-42de-82b4-88a0dd352090"]
    assert engineer.salary == Salary(min=204400, max=352000, currency="USD", interval="year")

    # Fixture: Account Executive has a compensation object with no components.
    assert postings["1515fe6d-1d8e-475b-a5ee-cefe43e78cb7"].salary is None


def test_404_raises_board_not_found() -> None:
    def not_found(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404)

    client = AshbyClient(httpx2.Client(transport=httpx2.MockTransport(not_found)))
    with pytest.raises(BoardNotFound):
        client.fetch_postings("nonexistent", "Nobody")


def test_client_for_returns_ashby() -> None:
    client = client_for("ashby", httpx2.Client())
    assert isinstance(client, AshbyClient)
    assert client.ats == "ashby"
