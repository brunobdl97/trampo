"""The daily Run: resolve the newest Opus, recover in-flight judge batches,
Discovery, collect Postings from every Board, pre-filter, dedupe into Jobs,
close/reject, judge through the Batch API, send the Digest, record the Run.

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
    SpendLimitReached,
    batch_results,
    load_prompt,
    newest_opus,
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
from trampo.models import Ats, VerdictValue
from trampo.prefilter import Dropped, prefilter
from trampo.resume.model import load_resume
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
    """Collect every judge batch a previous Run left open, with the model and
    prompt it was submitted with. One still processing keeps its Jobs for later."""
    for batch_id, _, model_id, prompt_hash in ctx.store.open_batches("judge"):
        results = batch_results(ctx.claude, batch_id)
        if results is None:
            logger.info("Judge batch %s still processing; its Jobs wait", batch_id)
            continue
        logger.info("Recovered judge batch %s", batch_id)
        _save_results(ctx, batch_id, results, model_id, prompt_hash, judged)


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
    batch_id = submit_batch(ctx.claude, requests)
    # Recorded before polling, so a Run that dies here leaves it recoverable.
    job_ids = [item.job.id for item in items]
    ctx.store.add_batch(batch_id, "judge", run_id, job_ids, model, prompt.hash, ctx.now())
    logger.info("Submitted judge batch %s with %d Jobs", batch_id, len(items))

    results = None
    while results is None:
        ctx.sleep(ctx.poll_interval)
        results = batch_results(ctx.claude, batch_id)
    _save_results(ctx, batch_id, results, model, prompt.hash, judged)


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


def _tally(summary: RunSummary, judged: dict[int, VerdictValue]) -> None:
    verdicts = Counter(judged.values())
    summary.eligible = verdicts["eligible"]
    summary.needs_review = verdicts["needs_review"]
    summary.rejected = verdicts["rejected"]


def _counts(summary: RunSummary) -> dict[str, int]:
    return {k: v for k, v in asdict(summary).items() if k != "run_id"}
