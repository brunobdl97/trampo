"""Eval (Task 19): re-judge every Job the Candidate overrode with the
current judge prompt and model, and measure agreement with the Candidate's
Override verdicts. Regular calls (not the Batch API) — `trampo eval` asks for
confirmation first, since each call costs money."""

from collections import Counter
from dataclasses import dataclass

import anthropic

from trampo.claude import create_message, load_prompt
from trampo.config import Config, Profile
from trampo.judge import judge_params, parse_judgment
from trampo.resume.model import Resume
from trampo.store import Store


@dataclass
class EvalReport:
    total: int
    agree: int
    confusion: dict[tuple[str, str], int]  # (override verdict, new verdict) -> count
    disagreements: list[tuple[int, str, str]]  # (job_id, expected, got)


def evaluate(
    client: anthropic.Anthropic,
    store: Store,
    config: Config,
    profile: Profile,
    resume: Resume,
    model: str,
) -> EvalReport:
    """Re-judge every overridden Job and compare to its Override's
    `to_verdict`. A refusal counts as "needs_review" (the pipeline's rule); a
    ValueError (invalid Judgment) counts as "error", always a disagreement.
    SpendLimitReached propagates."""
    prompt = load_prompt("judge")
    total = 0
    agree = 0
    confusion: Counter[tuple[str, str]] = Counter()
    disagreements: list[tuple[int, str, str]] = []
    for item, expected in store.overridden_jobs():
        params = judge_params(item, resume, config, profile, prompt, model)
        message = create_message(client, params)
        try:
            judgment = parse_judgment(message)
        except ValueError:
            got = "error"
        else:
            got = "needs_review" if judgment is None else judgment.verdict

        total += 1
        if got == expected:
            agree += 1
        else:
            disagreements.append((item.job.id, expected, got))
        confusion[(expected, got)] += 1
    return EvalReport(
        total=total, agree=agree, confusion=dict(confusion), disagreements=disagreements
    )
