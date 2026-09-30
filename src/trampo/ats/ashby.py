"""Ashby client: GET /posting-api/job-board/{slug}?includeCompensation=true."""

from datetime import UTC, datetime
from typing import Any

import httpx2

from trampo.ats import BoardNotFound
from trampo.models import Ats, Posting, Salary, Workplace

_BASE_URL = "https://api.ashbyhq.com"

_WORKPLACE: dict[str, Workplace] = {"Remote": "remote", "Hybrid": "hybrid", "OnSite": "onsite"}
_INTERVAL: dict[str, str] = {"1 YEAR": "year", "1 MONTH": "month", "1 HOUR": "hour"}


def _location(job: dict[str, Any]) -> str | None:
    parts: list[str] = []
    primary = job.get("location")
    if primary:
        parts.append(primary)
    for secondary in job.get("secondaryLocations") or []:
        loc = secondary.get("location")
        if loc and loc not in parts:
            parts.append(loc)
    return "; ".join(parts) if parts else None


def _workplace(job: dict[str, Any]) -> Workplace | None:
    workplace_type: str | None = job.get("workplaceType")
    if workplace_type:
        return _WORKPLACE.get(workplace_type)
    return "remote" if job.get("isRemote") else None


def _salary(compensation: dict[str, Any] | None) -> Salary | None:
    for component in (compensation or {}).get("summaryComponents") or []:
        if component.get("compensationType") == "Salary":
            interval: str = component["interval"]
            return Salary(
                min=component.get("minValue"),
                max=component.get("maxValue"),
                currency=component["currencyCode"],
                interval=_INTERVAL.get(interval, interval),
            )
    return None


class AshbyClient:
    ats: Ats = "ashby"

    def __init__(self, http: httpx2.Client) -> None:
        self._http = http

    def fetch_postings(self, slug: str, company: str) -> list[Posting]:
        response = self._http.get(
            f"{_BASE_URL}/posting-api/job-board/{slug}", params={"includeCompensation": "true"}
        )
        if response.status_code == 404:
            raise BoardNotFound(slug)
        response.raise_for_status()
        jobs: list[dict[str, Any]] = response.json()["jobs"]
        return [
            self._to_posting(job, slug, company) for job in jobs if job.get("isListed") is not False
        ]

    def _to_posting(self, job: dict[str, Any], slug: str, company: str) -> Posting:
        published_at_raw = job.get("publishedAt")
        published_at = (
            datetime.fromisoformat(published_at_raw).astimezone(UTC) if published_at_raw else None
        )
        return Posting(
            ats=self.ats,
            board_slug=slug,
            posting_id=job["id"],
            company=company,
            title=job["title"],
            location=_location(job),
            url=job["jobUrl"],
            description=job.get("descriptionPlain") or "",
            workplace=_workplace(job),
            salary=_salary(job.get("compensation")),
            published_at=published_at,
        )
