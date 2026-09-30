"""Dedupe Postings into Jobs: normalize a title, then find or create the Job it belongs to.

See CONTEXT.md: a Job groups one or more Postings by same company + normalized title;
a Repost is a new Posting for a Job already seen within the Repost window.
"""

import re
import string
import unicodedata
from datetime import datetime, timedelta

from trampo.models import Posting
from trampo.store import Store

_LOCATION_WORDS = {
    "remote",
    "brazil",
    "brasil",
    "latam",
    "americas",
    "worldwide",
    "anywhere",
    "global",
    "argentina",
    "mexico",
    "colombia",
    "chile",
    "us",
    "usa",
    "canada",
    "emea",
}
_BRACKETS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_SUFFIX_SEPARATORS = (" - ", " – ", " | ", ", ")
_WORD_SPLIT = re.compile(r"[\s,/-]+")
_STRIP_CHARS = string.punctuation + string.whitespace


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _BRACKETS.sub(" ", text)
    while True:
        sep_idx, sep = max((text.rfind(sep), sep) for sep in _SUFFIX_SEPARATORS)
        if sep_idx == -1:
            break
        head, tail = text[:sep_idx], text[sep_idx + len(sep) :]
        words = [w for w in _WORD_SPLIT.split(tail.strip()) if w]
        if not words or not all(w in _LOCATION_WORDS for w in words):
            break
        text = head
    text = " ".join(text.split())
    return text.strip(_STRIP_CHARS)


def assign_job(
    store: Store, posting: Posting, run_id: int, now: datetime, repost_days: int
) -> tuple[int, bool]:
    known_job_id = store.posting_job_id(posting.ats, posting.posting_id)
    if known_job_id is not None:
        return known_job_id, False

    normalized_title = normalize_title(posting.title)
    since = now - timedelta(days=repost_days)
    recent_job_id = store.find_recent_job(posting.company, normalized_title, since)
    if recent_job_id is not None:
        return recent_job_id, False

    job_id = store.create_job(posting.company, posting.title, normalized_title, run_id, now)
    return job_id, True
