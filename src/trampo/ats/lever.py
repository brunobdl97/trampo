"""Lever client: GET /v0/postings/{slug}?mode=json."""

from datetime import UTC, datetime
from typing import Any

import httpx2

from trampo.ats import BoardNotFound, html_to_text
from trampo.models import Ats, Posting, Salary, Workplace

_BASE_URL = "https://api.lever.co"

_WORKPLACE: dict[str, Workplace] = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite"}
_INTERVAL: dict[str, str] = {
    "per-year-salary": "year",
    "per-month-salary": "month",
    "per-hour-wage": "hour",
}


def _description(job: dict[str, Any]) -> str:
    parts: list[str] = []
    description_plain = (job.get("descriptionPlain") or "").strip()
    if description_plain:
        parts.append(description_plain)
    for item in job.get("lists") or []:
        text = (item.get("text") or "").strip()
        if text:
            parts.append(text)
        content = html_to_text(item.get("content") or "")
        if content:
            parts.append(content)
    additional_plain = (job.get("additionalPlain") or "").strip()
    if additional_plain:
        parts.append(additional_plain)
    return "\n\n".join(parts)


def _salary(salary_range: dict[str, Any] | None) -> Salary | None:
    if salary_range is None:
        return None
    interval: str = salary_range["interval"]
    return Salary(
        min=salary_range.get("min"),
        max=salary_range.get("max"),
        currency=salary_range["currency"],
        interval=_INTERVAL.get(interval, interval),
    )


class LeverClient:
    ats: Ats = "lever"

    def __init__(self, http: httpx2.Client) -> None:
        self._http = http

    def fetch_postings(self, slug: str, company: str) -> list[Posting]:
        response = self._http.get(f"{_BASE_URL}/v0/postings/{slug}", params={"mode": "json"})
        if response.status_code == 404:
            raise BoardNotFound(slug)
        response.raise_for_status()
        jobs: list[dict[str, Any]] = response.json()
        return [self._to_posting(job, slug, company) for job in jobs]

    def _to_posting(self, job: dict[str, Any], slug: str, company: str) -> Posting:
        workplace_type: str = job.get("workplaceType") or ""
        return Posting(
            ats=self.ats,
            board_slug=slug,
            posting_id=job["id"],
            company=company,
            title=job["text"],
            location=job.get("categories", {}).get("location"),
            url=job["hostedUrl"],
            description=_description(job),
            workplace=_WORKPLACE.get(workplace_type),
            salary=_salary(job.get("salaryRange")),
            published_at=datetime.fromtimestamp(job["createdAt"] / 1000, UTC),
        )
