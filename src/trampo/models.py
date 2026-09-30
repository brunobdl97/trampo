"""Domain models: Board, Posting, Job, Verdict/Judgment.

Shared across the pipeline, the Store and the web UI.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from trampo.config import TrackId

Ats = Literal["greenhouse", "lever", "ashby"]
VerdictValue = Literal["pending", "eligible", "needs_review", "rejected"]
StatusValue = Literal["new", "seen", "applied", "dismissed"]
Workplace = Literal["remote", "hybrid", "onsite"]


class BoardRef(BaseModel, frozen=True):
    ats: Ats
    slug: str


class Salary(BaseModel):
    min: float | None
    max: float | None
    currency: str
    interval: str  # "year" | "month" | "hour"


class Posting(BaseModel):
    ats: Ats
    board_slug: str
    posting_id: str
    company: str
    title: str
    location: str | None
    url: str
    description: str  # plain text
    workplace: Workplace | None
    salary: Salary | None
    published_at: datetime | None  # None when the ATS gives no reliable date


class Judgment(BaseModel):  # Claude's structured output (Task 11)
    track: TrackId
    verdict: Literal["eligible", "needs_review", "rejected"]
    reason: str  # pt-BR
    remote: bool
    open_to_brazil: bool | None
    requires_us_work_authorization: bool | None
    fit_score: int = Field(ge=0, le=10)
    fit_reason: str  # pt-BR
    job_language: Literal["en", "pt"]


class JobRow(BaseModel):
    id: int
    company: str
    title: str
    normalized_title: str
    created_run_id: int
    created_at: datetime
    closed_at: datetime | None
    verdict: VerdictValue
    verdict_reason: str | None
    track: TrackId | None
    fit_score: int | None
    fit_reason: str | None
    job_language: str | None
    status: StatusValue
    notes: str
    resume_path: str | None
    workplace: Workplace | None = None  # the most recently seen Posting's
    published_at: datetime | None = None  # earliest non-null among its Postings
    salary: Salary | None = None  # the most recently seen Posting that has one
    locations: list[str]  # from its Postings
    urls: list[str]  # from its Postings


@dataclass(frozen=True)
class JobWithPostings:
    job: JobRow
    postings: list[Posting]
