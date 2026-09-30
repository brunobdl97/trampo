"""Build one Messages API request per Job (Task 11) and parse Claude's
structured Judgment back out of the response.

The request's `system` blocks — rendered instructions plus the Base resume —
depend only on Config, Profile and the Base resume, never on the Job, so
they are byte-identical for every Job in a Run: that is what makes the
`cache_control` breakpoint on the last block actually hit.
"""

from collections.abc import Sequence

import anthropic
from anthropic.types import Message

from trampo.claude import Prompt
from trampo.config import Config, Profile, SalaryFloor, Track
from trampo.models import JobWithPostings, Judgment, Posting, Salary
from trampo.resume.model import Resume

_CONTRACT_NAMES = {
    "clt": "CLT",
    "pj": "PJ",
    "international_contractor": "international contractor",
}


def _render_tracks(tracks: list[Track]) -> str:
    lines = []
    for track in tracks:
        keywords = ", ".join(f"`{kw}`" for kw in track.title_keywords)
        line = f"- **{track.name}** (id: `{track.id}`): title keywords {keywords}"
        if track.title_keywords_requiring_go:
            go_keywords = ", ".join(f"`{kw}`" for kw in track.title_keywords_requiring_go)
            line += f"; also {go_keywords} when the description mentions Go"
        line += f". Tailored resume emphasis: {track.emphasis}."
        lines.append(line)
    return "\n".join(lines)


def _render_salary_floor(floor: SalaryFloor) -> str:
    return (
        f"- CLT: R$ {floor.clt_brl:,}/month\n"
        f"- PJ: R$ {floor.pj_brl:,}/month\n"
        f"- International contractor: US$ {floor.international_usd:,}/month"
    )


def _render_accepted_contracts(contracts: Sequence[str]) -> str:
    return ", ".join(_CONTRACT_NAMES[c] for c in contracts)


def _render_instructions(template: str, config: Config, profile: Profile) -> str:
    text = template
    text = text.replace("{{tracks}}", _render_tracks(config.tracks))
    text = text.replace("{{salary_floor}}", _render_salary_floor(profile.salary_floor))
    text = text.replace(
        "{{accepted_contracts}}", _render_accepted_contracts(profile.accepted_contracts)
    )
    return text


def _format_salary(salary: Salary | None) -> str:
    if salary is None:
        return "not published"
    if salary.min is not None and salary.max is not None:
        amount = f"{salary.min:g}–{salary.max:g}"
    elif salary.min is not None:
        amount = f"from {salary.min:g}"
    elif salary.max is not None:
        amount = f"up to {salary.max:g}"
    else:
        amount = "unspecified amount"
    return f"{amount} {salary.currency}/{salary.interval}"


def _format_published(posting: Posting) -> str:
    if posting.published_at is None:
        return "unknown"
    return posting.published_at.date().isoformat()


def _user_message(item: JobWithPostings) -> str:
    latest = item.postings[-1]
    locations = ", ".join(item.job.locations)
    return (
        f"Company: {item.job.company}\n"
        f"Title: {item.job.title}\n"
        f"Locations: {locations}\n"
        f"Workplace: {latest.workplace or 'not specified'}\n"
        f"Salary: {_format_salary(latest.salary)}\n"
        f"Published: {_format_published(latest)}\n"
        f"Description:\n{latest.description}\n"
    )


def judge_params(
    item: JobWithPostings,
    resume: Resume,
    config: Config,
    profile: Profile,
    prompt: Prompt,
    model: str,
) -> dict:
    """Messages API params to judge one Job. `system` is byte-identical for
    every Job in a Run (given the same config/profile/resume), so the
    `cache_control` breakpoint on its last block hits after the first call."""
    instructions = _render_instructions(prompt.text, config, profile)
    return {
        "model": model,
        "max_tokens": 16000,
        "thinking": {"type": "adaptive"},
        "system": [
            {"type": "text", "text": instructions},
            {
                "type": "text",
                "text": resume.model_dump_json(),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        "messages": [{"role": "user", "content": _user_message(item)}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": anthropic.transform_schema(Judgment),
            }
        },
    }


def parse_judgment(message: Message) -> Judgment | None:
    """The Judgment Claude produced, or None on a refusal (`stop_reason ==
    "refusal"`). Every other failure — invalid/out-of-range JSON, or no text
    block at all (e.g. `max_tokens` truncation) — raises ValueError (Pydantic's
    ValidationError is a ValueError subclass), so the pipeline can catch one
    type for both."""
    if message.stop_reason == "refusal":
        return None
    text = next((block.text for block in message.content if block.type == "text"), None)
    if text is None:
        raise ValueError(f"no text block in judge response (stop_reason={message.stop_reason!r})")
    return Judgment.model_validate_json(text)
