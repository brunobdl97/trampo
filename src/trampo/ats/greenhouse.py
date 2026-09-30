"""Greenhouse client: GET /v1/boards/{slug}/jobs?content=true."""

import html
from datetime import UTC, datetime
from typing import Any

import httpx2

from trampo.ats import BoardNotFound, html_to_text
from trampo.models import Ats, Posting

_BASE_URL = "https://boards-api.greenhouse.io"


class GreenhouseClient:
    ats: Ats = "greenhouse"

    def __init__(self, http: httpx2.Client) -> None:
        self._http = http

    def fetch_postings(self, slug: str, company: str) -> list[Posting]:
        response = self._http.get(f"{_BASE_URL}/v1/boards/{slug}/jobs", params={"content": "true"})
        if response.status_code == 404:
            raise BoardNotFound(slug)
        response.raise_for_status()
        jobs: list[dict[str, Any]] = response.json()["jobs"]
        return [self._to_posting(job, slug, company) for job in jobs]

    def _to_posting(self, job: dict[str, Any], slug: str, company: str) -> Posting:
        first_published = job.get("first_published")
        published_at = (
            datetime.fromisoformat(first_published).astimezone(UTC) if first_published else None
        )
        return Posting(
            ats=self.ats,
            board_slug=slug,
            posting_id=str(job["id"]),
            company=company,
            title=job["title"],
            location=job["location"]["name"],
            url=job["absolute_url"],
            description=html_to_text(html.unescape(job["content"])),
            workplace=None,
            salary=None,
            published_at=published_at,
        )
