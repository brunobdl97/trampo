"""Tailored resumes: the request that asks Claude to rewrite the Base resume
for one Job, the guard that refuses any fact absent from the Base resume, and
saving the result as a PDF.

The request's `system` blocks — instructions plus the Base resume — never
depend on the Job, its Track or the language, so the `cache_control`
breakpoint on the last one hits across every Tailored resume.
"""

import logging
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import anthropic

from trampo.claude import (
    Prompt,
    create_message,
    load_prompt,
    newest_opus,
    parse_structured,
)
from trampo.config import Config, Paths, Track
from trampo.judge import format_job
from trampo.models import JobWithPostings
from trampo.prefilter import matches_word, title_track
from trampo.resume.model import Location, Resume, load_resume
from trampo.resume.render import html_to_pdf, render_html, resume_filename
from trampo.store import Store

if TYPE_CHECKING:
    from trampo.pipeline import RunContext  # pipeline imports this module

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_LANGUAGES = {"en": "English", "pt": "Brazilian Portuguese"}


class InventedFactsError(Exception):
    """The guard refused a Tailored resume: it states facts absent from the Base resume."""

    def __init__(self, facts: list[str]) -> None:
        super().__init__("facts absent from the Base resume: " + "; ".join(facts))
        self.facts = facts


class TailorRefused(Exception):
    """Claude refused to write the Tailored resume (`stop_reason == "refusal"`)."""


def _strings(value: Any) -> Iterator[str]:
    """Every string in a `model_dump()`, walking dicts and lists."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _numbers(text: str) -> list[str]:
    """The numbers in `text`, with `,` and `.` treated alike (1,000 = 1.000, 3,5 = 3.5)."""
    return [number.replace(",", ".") for number in _NUMBER.findall(text)]


def _same(text: str) -> str:
    """How verbatim fields compare: case and surrounding spaces are not facts."""
    return text.strip().casefold()


def _by_name[T](entries: Iterable[T], name: Callable[[T], str]) -> dict[str, list[T]]:
    grouped: dict[str, list[T]] = {}
    for entry in entries:
        grouped.setdefault(_same(name(entry)), []).append(entry)
    return grouped


def invented_facts(base: Resume, tailored: Resume) -> list[str]:
    """The facts `tailored` states that `base` doesn't; an empty list means OK.
    Reordering, omitting, rephrasing and translating prose are all allowed;
    names, positions, dates, contact data, URLs and skill keywords stay verbatim."""
    flagged: list[str] = []

    # A work entry must match ONE base entry at its company as a whole: a
    # promotion is two entries at one company, and taking the dates of one and
    # the title of the other invents a title.
    base_work = _by_name(base.work, lambda w: w.name)
    for work in tailored.work:
        matches = base_work.get(_same(work.name))
        facts = (work.startDate, work.endDate, _same(work.position))
        if matches is None:
            flagged.append(f"company not in the Base resume: {work.name}")
        elif all((m.startDate, m.endDate, _same(m.position)) != facts for m in matches):
            end = work.endDate or "present"
            flagged.append(
                f"work entry not in the Base resume: {work.position} at {work.name}, "
                f"{work.startDate} to {end}"
            )

    b, t = base.basics, tailored.basics
    t_location, b_location = t.location or Location(), b.location or Location()
    for field, value, base_value in (
        ("name", t.name, b.name),
        ("email", t.email, b.email),
        ("phone", t.phone, b.phone),
        ("label", t.label, b.label),
        ("city", t_location.city, b_location.city),
        ("country code", t_location.countryCode, b_location.countryCode),
    ):
        if value and _same(value) != _same(base_value or ""):  # omitted is fine
            flagged.append(f"{field} changed: {value}")
    base_urls = {_same(profile.url) for profile in b.profiles}
    for profile in t.profiles:
        if _same(profile.url) not in base_urls:
            flagged.append(f"profile not in the Base resume: {profile.url}")

    base_education = _by_name(base.education, lambda e: e.institution)
    for education in tailored.education:
        matches = base_education.get(_same(education.institution))
        dates = (education.startDate, education.endDate)
        if matches is None:
            flagged.append(f"institution not in the Base resume: {education.institution}")
        elif all((m.startDate, m.endDate) != dates for m in matches):
            flagged.append(f"dates changed for {education.institution}: {dates[0]} to {dates[1]}")

    base_projects = _by_name(base.projects, lambda p: p.name)
    for project in tailored.projects:
        matches = base_projects.get(_same(project.name))
        if matches is None:
            flagged.append(f"project not in the Base resume: {project.name}")
        # An omitted URL is fine; a changed or added one is not.
        elif project.url and all(_same(project.url) != _same(m.url or "") for m in matches):
            flagged.append(f"project URL changed for {project.name}: {project.url}")

    base_strings = list(_strings(base.model_dump()))
    base_numbers = {n for text in base_strings for n in _numbers(text)}
    tailored_numbers = (n for text in _strings(tailored.model_dump()) for n in _numbers(text))
    for number in dict.fromkeys(tailored_numbers):  # deduplicated, in order
        if number not in base_numbers:
            flagged.append(f"number not in the Base resume: {number}")

    base_text = "\n".join(base_strings)
    for skill in tailored.skills:
        for keyword in skill.keywords:
            if not matches_word(keyword, base_text):
                flagged.append(f"skill keyword not in the Base resume: {keyword}")
    return flagged


def resume_language(job_language: str | None) -> Literal["en", "pt"]:
    return "pt" if job_language == "pt" else "en"


def job_track(item: JobWithPostings, config: Config) -> Track:
    """The Job's Track; from its title if it has none yet; else the first Track."""
    track_id = item.job.track or title_track(item.job.title, item.postings[-1].description, config)
    return next((t for t in config.tracks if t.id == track_id), config.tracks[0])


def tailor_params(
    item: JobWithPostings,
    resume: Resume,
    track: Track,
    prompt: Prompt,
    model: str,
    lang: Literal["en", "pt"],
) -> dict:
    """Messages API params to tailor `resume` for one Job; see the module docstring."""
    job = (
        f"{format_job(item)}\n"
        f"Track: {track.name}; emphasize {track.emphasis}.\n"
        f"Write the Tailored resume in {_LANGUAGES[lang]}.\n"
    )
    return {
        "model": model,
        "max_tokens": 16000,
        "thinking": {"type": "adaptive"},
        "system": [
            {"type": "text", "text": prompt.text},
            {
                "type": "text",
                "text": resume.model_dump_json(),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [{"role": "user", "content": job}],
        "output_config": {
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(Resume)}
        },
    }


def save_tailored(
    store: Store,
    paths: Paths,
    job_id: int,
    tailored: Resume,
    base: Resume,
    model: str,
    prompt: Prompt,
) -> Path:
    """Guard, render, write the PDF, record it on the Job. A refused Tailored
    resume raises InventedFactsError and leaves no file and no Store change."""
    flagged = invented_facts(base, tailored)
    if flagged:
        logger.warning("Job %d: Tailored resume refused: %s", job_id, "; ".join(flagged))
        raise InventedFactsError(flagged)
    job = store.job(job_id).job
    path = paths.resumes_dir / resume_filename(job.company, job.title)
    html_to_pdf(render_html(tailored, resume_language(job.job_language)), path)
    store.save_resume(job_id, path, model, prompt.hash)
    return path


def tailor_now(ctx: RunContext, job_id: int) -> Path:
    """An on-demand Tailored resume through a regular call (the Candidate is
    waiting). JobNotFound, TailorRefused, InventedFactsError and
    SpendLimitReached propagate."""
    item = ctx.store.job(job_id)
    model = newest_opus(ctx.claude)
    prompt = load_prompt("tailor")
    base = load_resume(ctx.paths.resume_json)
    params = tailor_params(
        item,
        base,
        job_track(item, ctx.config),
        prompt,
        model,
        resume_language(item.job.job_language),
    )
    tailored = parse_structured(create_message(ctx.claude, params), Resume)
    if tailored is None:
        raise TailorRefused(f"Job {job_id}: the model refused to tailor the resume")
    return save_tailored(ctx.store, ctx.paths, job_id, tailored, base, model, prompt)
