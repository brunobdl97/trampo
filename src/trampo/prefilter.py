"""Objective, code-only rules applied to each Posting before Claude judges it.

Each rule is case-insensitive and matches on word boundaries so substrings
inside other words never trigger (e.g. "internal" must not match "intern",
"django engineer" must not match the "go engineer" keyword).
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from trampo.config import Config, TrackId
from trampo.models import Posting

_GO_WORD = re.compile(r"(?<![\w-])Go(?![\w-])")


@dataclass(frozen=True)
class Dropped:
    reason: str  # English, for logs and Run counts


def _matches(keyword: str, text: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def mentions_go(description: str) -> bool:
    return "golang" in description.lower() or bool(_GO_WORD.search(description))


def title_track(title: str, description: str, config: Config) -> TrackId | None:
    for track in config.tracks:
        if any(_matches(kw, title) for kw in track.title_keywords):
            return track.id
        if any(_matches(kw, title) for kw in track.title_keywords_requiring_go) and mentions_go(
            description
        ):
            return track.id
    return None


def prefilter(posting: Posting, now: datetime, config: Config) -> TrackId | Dropped:
    for kw in config.ignore_title_keywords:
        if _matches(kw, posting.title):
            return Dropped(f"ignored title keyword: {kw}")

    track = title_track(posting.title, posting.description, config)
    if track is None:
        return Dropped("no track keyword")

    if posting.workplace in ("hybrid", "onsite"):
        return Dropped("not remote")

    published = posting.published_at or now
    if now - published > timedelta(days=config.max_age_days):
        return Dropped(f"older than {config.max_age_days} days")

    return track
