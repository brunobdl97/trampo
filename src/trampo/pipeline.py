"""The daily Run: resolve the newest Opus, recover in-flight batches,
Discovery, collect Postings from every Board, pre-filter, dedupe into Jobs,
close/reject, judge through the Batch API, send the Digest, automatic Tailored
resumes through a second batch, record the Run.

Failure modes handled here (backlog.md, Review Focus): an ATS outage never
closes that Board's Postings; the spend limit skips the remaining Claude steps
but the Run goes on and alerts once; a batch left in flight by a dead process
is collected by the next Run, never resubmitted.
"""

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime

import anthropic
from anthropic.types import Message

from trampo.ats import AtsClient, BoardNotFound
from trampo.claude import (
    Prompt,
    SpendLimitReached,
    batch_results,
    load_prompt,
    newest_opus,
    parse_structured,
    submit_batch,
)
from trampo.config import Config, Paths, Profile
from trampo.dedup import assign_job
from trampo.digest import (
    Telegram,
    TelegramError,
    format_digest,
    format_failure,
    format_spend_cap_alert,
)
from trampo.discovery import discover
from trampo.judge import judge_params, parse_judgment
from trampo.models import Ats, JobRow, VerdictValue
from trampo.prefilter import Dropped, prefilter
from trampo.resume.model import Resume, load_resume
from trampo.resume.tailor import (
    InventedFactsError,
    job_track,
    resume_language,
    save_tailored,
    tailor_params,
)
from trampo.store import Store

logger = logging.getLogger(__name__)

REFUSAL_REASON = "análise recusada pelo modelo"
CLOSED_REASON = "vaga encerrada"


@dataclass
class RunContext:
    store: Store
    config: Config
    profile: Profile
    paths: Paths
    claude: anthropic.Anthropic
    ats_clients: dict[Ats, AtsClient]
    telegram: Telegram
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    poll_interval: float = 30.0


@dataclass
class RunSummary:
    run_id: int
    new_jobs: int = 0
    eligible: int = 0  # eligible/needs_review/rejected: Verdicts this Run's Judgments set
    needs_review: int = 0
    rejected: int = 0
    pending: int = 0  # Jobs still pending and open at the end of the Run
    board_errors: int = 0


def run(ctx: RunContext) -> RunSummary:
    summary: RunSummary | None = None
    model: str | None = None
    judged: dict[int, VerdictValue] = {}  # job id -> Verdict set by a Judgment this Run
    try:
        summary = RunSummary(run_id=ctx.store.start_run(ctx.now()))

        # 1-3. Claude steps before collecting; the first spend-limit error skips the rest.
        spend_limit: SpendLimitReached | None = None
        try:
            model = newest_opus(ctx.claude)
            _recover(ctx, judged)
            _discover(ctx, model)
        except SpendLimitReached as exc:
            spend_limit = exc

        # 4-5. Collect
        _collect(ctx, summary)

        # 6. Close Jobs whose Postings all closed; a closed Job never gets judged.
        ctx.store.close_jobs_without_open_postings(ctx.now())
        ctx.store.reject_closed_pending(CLOSED_REASON)

        # 7. Judge
        if model is not None and spend_limit is None:
            try:
                _judge(ctx, summary.run_id, model, judged)
            except SpendLimitReached as exc:
                spend_limit = exc
        if spend_limit is not None:
            logger.warning("Spend limit reached, judging skipped: %s", spend_limit)
            send_alert(ctx.telegram, format_spend_cap_alert(ctx.store.pending_open_count()))

        # 8. Digest: every still-open Job that became eligible this Run, recovered
        # batches included (a recovered Job may have closed at step 6).
        _tally(summary, judged)
        jobs = [ctx.store.job(job_id).job for job_id, v in judged.items() if v == "eligible"]
        eligible = [job for job in jobs if job.closed_at is None]
        if eligible:
            for message in format_digest(eligible, summary.needs_review, summary.rejected):
                ctx.telegram.send(message)

        # 8a. Automatic Tailored resumes, in their own batch after the Digest.
        if model is not None and spend_limit is None:
            try:
                _tailor(ctx, summary.run_id, model, eligible)
            except SpendLimitReached as exc:  # the Run's first: alert once all the same
                logger.warning("Spend limit reached, Tailored resumes skipped: %s", exc)
                send_alert(ctx.telegram, format_spend_cap_alert(ctx.store.pending_open_count()))

        # 9. Record the Run
        summary.pending = ctx.store.pending_open_count()
        ctx.store.finish_run(summary.run_id, ctx.now(), "ok", _counts(summary), None, model)
        logger.info("Run %d finished: %s", summary.run_id, _counts(summary))
        return summary
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if summary is None:
            logger.exception("Run failed to start")
        else:
            logger.exception("Run %d failed", summary.run_id)
            _tally(summary, judged)
            try:
                ctx.store.finish_run(
                    summary.run_id, ctx.now(), "failed", _counts(summary), error, model
                )
            except Exception:
                logger.exception("Could not record the failed Run %d", summary.run_id)
        send_alert(ctx.telegram, format_failure(error))
        raise


def send_alert(telegram: Telegram, text: str) -> None:
    """Send a Telegram alert; a Telegram failure is logged, never raised, so it
    neither stops the Run nor masks the error being reported."""
    try:
        telegram.send(text)
    except TelegramError:
        logger.exception("Could not send a Telegram alert")


def _recover(ctx: RunContext, judged: dict[int, VerdictValue]) -> None:
    """Collect every judge and resume batch a previous Run left open, with the
    model and prompt it was submitted with. One still processing keeps its Jobs
    for later."""
    for kind in ("judge", "resume"):
        for batch_id, _, model_id, prompt_hash in ctx.store.open_batches(kind):
            results = batch_results(ctx.claude, batch_id)
            if results is None:
                logger.info("%s batch %s still processing; its Jobs wait", kind, batch_id)
                continue
            logger.info("Recovered %s batch %s", kind, batch_id)
            if kind == "judge":
                _save_results(ctx, batch_id, results, model_id, prompt_hash, judged)
            else:
                _save_resumes(ctx, batch_id, results, model_id, prompt_hash)


def _discover(ctx: RunContext, model: str) -> None:
    """Discovery is best-effort: only the spend limit escapes; any other
    failure is logged and the Run goes on with the Boards it already has."""
    try:
        discover(ctx.claude, ctx.store, ctx.config, ctx.ats_clients, ctx.now().date(), model)
    except SpendLimitReached:
        raise
    except Exception:
        logger.exception("Discovery failed; the Run goes on without it")


def _collect(ctx: RunContext, summary: RunSummary) -> None:
    for board, company in ctx.store.active_boards():
        try:
            postings = ctx.ats_clients[board.ats].fetch_postings(board.slug, company)
        except BoardNotFound:
            # A deleted Board lists nothing: close its Postings so step 6 closes its Jobs.
            logger.warning("Board %s/%s no longer exists; deactivated", board.ats, board.slug)
            ctx.store.deactivate_board(board)
            ctx.store.close_missing_postings(board, set(), ctx.now())
            continue
        except Exception:
            # Review Focus 1: an outage must not close this Board's Postings.
            logger.exception("Board %s/%s failed; its Postings stay open", board.ats, board.slug)
            summary.board_errors += 1
            continue

        now = ctx.now()
        for posting in postings:
            job_id = ctx.store.posting_job_id(posting.ats, posting.posting_id)
            if job_id is None:
                kept = prefilter(posting, now, ctx.config)
                if isinstance(kept, Dropped):
                    logger.debug("Dropped %s: %s", posting.url, kept.reason)
                    continue
                job_id, created = assign_job(
                    ctx.store, posting, summary.run_id, now, ctx.config.repost_days
                )
                summary.new_jobs += created
            ctx.store.upsert_posting(posting, job_id, now)
        ctx.store.close_missing_postings(board, {p.posting_id for p in postings}, now)


def _judge(ctx: RunContext, run_id: int, model: str, judged: dict[int, VerdictValue]) -> None:
    items = ctx.store.jobs_to_judge()
    if not items:
        return
    prompt = load_prompt("judge")
    resume = load_resume(ctx.paths.resume_json)
    requests = {
        f"judge-{item.job.id}": judge_params(item, resume, ctx.config, ctx.profile, prompt, model)
        for item in items
    }
    batch_id, results = _submit_and_poll(ctx, "judge", run_id, requests, model, prompt.hash)
    _save_results(ctx, batch_id, results, model, prompt.hash, judged)


def _tailor(ctx: RunContext, run_id: int, model: str, eligible: list[JobRow]) -> None:
    """One resume batch for the open Jobs that became eligible this Run at the
    Fit-score threshold and have no Tailored resume yet."""
    threshold = ctx.config.auto_resume_min_fit_score
    items = [
        ctx.store.job(job.id)
        for job in eligible
        if job.fit_score is not None and job.fit_score >= threshold and job.resume_path is None
    ]
    if not items:
        return
    prompt = load_prompt("tailor")
    base = load_resume(ctx.paths.resume_json)
    requests = {
        f"resume-{item.job.id}": tailor_params(
            item,
            base,
            job_track(item, ctx.config),
            prompt,
            model,
            resume_language(item.job.job_language),
        )
        for item in items
    }
    batch_id, results = _submit_and_poll(ctx, "resume", run_id, requests, model, prompt.hash)
    _save_resumes(ctx, batch_id, results, model, prompt.hash)


def _submit_and_poll(
    ctx: RunContext,
    kind: str,
    run_id: int,
    requests: dict[str, dict],
    model: str,
    prompt_hash: str,
) -> tuple[str, dict[str, Message | str]]:
    """Submit one batch (custom ids "<kind>-<job id>") and poll it until it ends."""
    batch_id = submit_batch(ctx.claude, requests)
    # Recorded before polling, so a Run that dies here leaves it recoverable.
    job_ids = [int(custom_id.removeprefix(f"{kind}-")) for custom_id in requests]
    ctx.store.add_batch(batch_id, kind, run_id, job_ids, model, prompt_hash, ctx.now())
    logger.info("Submitted %s batch %s with %d Jobs", kind, batch_id, len(job_ids))

    results = None
    while results is None:
        ctx.sleep(ctx.poll_interval)
        results = batch_results(ctx.claude, batch_id)
    return batch_id, results


def _save_results(
    ctx: RunContext,
    batch_id: str,
    results: dict[str, Message | str],
    model_id: str,
    prompt_hash: str,
    judged: dict[int, VerdictValue],
) -> None:
    for custom_id, result in results.items():
        job_id = int(custom_id.removeprefix("judge-"))
        verdict = ctx.store.job(job_id).job.verdict
        if verdict != "pending":  # closed ("vaga encerrada") or overridden meanwhile
            logger.info("Job %d already %s; its late Judgment is dropped", job_id, verdict)
            continue
        if isinstance(result, str):  # errored, canceled or expired
            logger.warning("Job %d stays pending: batch request %s", job_id, result)
            continue
        try:
            judgment = parse_judgment(result)
        except ValueError:
            logger.exception("Job %d stays pending: invalid Judgment", job_id)
            continue
        if judgment is None:
            ctx.store.set_verdict(job_id, "needs_review", REFUSAL_REASON, model_id, prompt_hash)
            judged[job_id] = "needs_review"
        else:
            ctx.store.save_judgment(job_id, judgment, model_id, prompt_hash)
            judged[job_id] = judgment.verdict
    ctx.store.mark_batch_collected(batch_id, ctx.now())


def _save_resumes(
    ctx: RunContext,
    batch_id: str,
    results: dict[str, Message | str],
    model_id: str,
    prompt_hash: str,
) -> None:
    base = load_resume(ctx.paths.resume_json)
    # save_tailored records only the prompt's hash: the one the batch was submitted with.
    prompt = Prompt(name="tailor", text="", hash=prompt_hash)
    for custom_id, result in results.items():
        job_id = int(custom_id.removeprefix("resume-"))
        if ctx.store.job(job_id).job.resume_path is not None:  # made on demand meanwhile
            logger.info("Job %d already has a Tailored resume; the batch's is dropped", job_id)
            continue
        if isinstance(result, str):  # errored, canceled or expired
            logger.warning("Job %d gets no Tailored resume: batch request %s", job_id, result)
            continue
        # One Job's failure (invalid output, the guard, rendering) skips only that
        # Job: a deterministic one, e.g. Chromium missing, must not fail every
        # later Run at recovery. The Job keeps no resume_path; on demand still works.
        try:
            tailored = parse_structured(result, Resume)
            if tailored is None:
                logger.warning("Job %d gets no Tailored resume: refused by the model", job_id)
                continue
            save_tailored(ctx.store, ctx.paths, job_id, tailored, base, model_id, prompt)
        except InventedFactsError:
            continue  # save_tailored logged the flagged facts
        except Exception:
            logger.exception("Job %d gets no Tailored resume", job_id)
    ctx.store.mark_batch_collected(batch_id, ctx.now())


def _tally(summary: RunSummary, judged: dict[int, VerdictValue]) -> None:
    verdicts = Counter(judged.values())
    summary.eligible = verdicts["eligible"]
    summary.needs_review = verdicts["needs_review"]
    summary.rejected = verdicts["rejected"]


def _counts(summary: RunSummary) -> dict[str, int]:
    return {k: v for k, v in asdict(summary).items() if k != "run_id"}
