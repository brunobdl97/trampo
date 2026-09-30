"""Lever client tests: parsing against a recorded fixture, no network."""

import json
from datetime import UTC
from pathlib import Path

import httpx
import pytest

from trampo.ats import BoardNotFound, client_for
from trampo.ats.lever import LeverClient
from trampo.models import Salary

FIXTURE = json.loads(Path("tests/fixtures/lever/postings.json").read_text())


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v0/postings/lyrahealth"
    assert request.url.params.get("mode") == "json"
    return httpx.Response(200, json=FIXTURE)


def _client() -> LeverClient:
    return LeverClient(httpx.Client(transport=httpx.MockTransport(_handler)))


def test_parses_postings() -> None:
    postings = _client().fetch_postings("lyrahealth", "Lyra Health")

    assert len(postings) == 3
    ids = {
        "2d759d20-802d-4447-bc53-2767dc590459",
        "35bffe8b-ac9f-4749-ba4c-5f3a4d2bd77d",
        "6f279008-c7d4-464a-aeab-21db398d79dd",
    }
    assert {p.posting_id for p in postings} == ids

    assistant = next(p for p in postings if p.posting_id == "2d759d20-802d-4447-bc53-2767dc590459")
    assert assistant.ats == "lever"
    assert assistant.board_slug == "lyrahealth"
    assert assistant.company == "Lyra Health"
    assert assistant.title == "Executive Assistant"
    assert assistant.location == "Santa Monica, California"
    assert assistant.url == (
        "https://jobs.lever.co/lyrahealth/2d759d20-802d-4447-bc53-2767dc590459"
    )
    assert assistant.workplace == "hybrid"
    assert assistant.salary == Salary(min=118000, max=162500, currency="USD", interval="year")


def test_description_is_plain_text_and_joins_parts() -> None:
    postings = _client().fetch_postings("lyrahealth", "Lyra Health")

    for posting in postings:
        assert "<" not in posting.description
        assert ">" not in posting.description
        assert "&nbsp;" not in posting.description

    assistant = next(p for p in postings if p.posting_id == "2d759d20-802d-4447-bc53-2767dc590459")
    # descriptionPlain, then each lists[] item's heading + body, then additionalPlain.
    assert assistant.description.startswith("About Lyra Health")
    assert "Responsibilities" in assistant.description
    assert "Qualifications" in assistant.description
    assert "Equal Opportunity Employer" in assistant.description  # from additionalPlain


def test_created_at_millis_to_utc() -> None:
    postings = {p.posting_id: p for p in _client().fetch_postings("lyrahealth", "Lyra Health")}

    assistant = postings["2d759d20-802d-4447-bc53-2767dc590459"]
    assert assistant.published_at is not None
    assert assistant.published_at.tzinfo == UTC
    assert assistant.published_at.isoformat() == "2026-09-23T22:20:07.955000+00:00"

    contractor = postings["35bffe8b-ac9f-4749-ba4c-5f3a4d2bd77d"]
    assert contractor.published_at is not None
    assert contractor.published_at.isoformat() == "2026-08-24T13:47:34.698000+00:00"


def test_salary_range() -> None:
    postings = {p.posting_id: p for p in _client().fetch_postings("lyrahealth", "Lyra Health")}

    assistant = postings["2d759d20-802d-4447-bc53-2767dc590459"]
    assert assistant.salary == Salary(min=118000, max=162500, currency="USD", interval="year")

    # Fixture: the other two postings have no salaryRange.
    assert postings["35bffe8b-ac9f-4749-ba4c-5f3a4d2bd77d"].salary is None
    assert postings["6f279008-c7d4-464a-aeab-21db398d79dd"].salary is None


def test_workplace_mapping() -> None:
    # Fixture covers remote/hybrid/onsite; "unspecified" and a missing key are
    # synthesized here since no real posting in the recorded board had them.
    payload = [
        {
            "id": "r1",
            "text": "Remote role",
            "categories": {},
            "hostedUrl": "https://jobs.lever.co/acme/r1",
            "createdAt": 1700000000000,
            "workplaceType": "remote",
        },
        {
            "id": "h1",
            "text": "Hybrid role",
            "categories": {},
            "hostedUrl": "https://jobs.lever.co/acme/h1",
            "createdAt": 1700000000000,
            "workplaceType": "hybrid",
        },
        {
            "id": "o1",
            "text": "Onsite role",
            "categories": {},
            "hostedUrl": "https://jobs.lever.co/acme/o1",
            "createdAt": 1700000000000,
            "workplaceType": "on-site",
        },
        {
            "id": "u1",
            "text": "Unspecified role",
            "categories": {},
            "hostedUrl": "https://jobs.lever.co/acme/u1",
            "createdAt": 1700000000000,
            "workplaceType": "unspecified",
        },
        {
            "id": "m1",
            "text": "Missing key role",
            "categories": {},
            "hostedUrl": "https://jobs.lever.co/acme/m1",
            "createdAt": 1700000000000,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = LeverClient(httpx.Client(transport=httpx.MockTransport(handler)))
    postings = {p.posting_id: p for p in client.fetch_postings("acme", "Acme")}

    assert postings["r1"].workplace == "remote"
    assert postings["h1"].workplace == "hybrid"
    assert postings["o1"].workplace == "onsite"
    assert postings["u1"].workplace is None
    assert postings["m1"].workplace is None


def test_404_raises_board_not_found() -> None:
    def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = LeverClient(httpx.Client(transport=httpx.MockTransport(not_found)))
    with pytest.raises(BoardNotFound):
        client.fetch_postings("nonexistent", "Nobody")


def test_client_for_returns_lever() -> None:
    client = client_for("lever", httpx.Client())
    assert isinstance(client, LeverClient)
    assert client.ats == "lever"

    with pytest.raises(NotImplementedError):
        client_for("ashby", httpx.Client())
