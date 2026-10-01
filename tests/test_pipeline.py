"""pipeline.py tests: whole Runs against MockTransport fakes of the ATS APIs,
the Anthropic API and Telegram — no network. The Greenhouse fixture is the
recorded one; the Anthropic batch and Message shapes follow the hand-built
fixtures under tests/fixtures/anthropic/ (see tests/test_claude.py)."""

import copy
import json
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from trampo import pipeline
from trampo.ats import client_for
from trampo.claude import load_prompt, make_client
from trampo.config import load_config, load_profile, private_paths
from trampo.digest import Telegram, format_digest, format_failure, format_spend_cap_alert
from trampo.models import BoardRef
from trampo.pipeline import RunContext, RunSummary, run
from trampo.resume import tailor
from trampo.store import Store

FIXTURES = Path("tests/fixtures")
GITLAB = json.loads((FIXTURES / "greenhouse/jobs.json").read_text())
# Of GITLAB's three Postings only this one passes the prefilter at NOW (the
# other two are "Engineering Manager" titles).
GITLAB_POSTING = "8644569002"
GITLAB_TITLE = "Intermediate Backend Engineer, Platform Readiness"
ACME_POSTING = "7000000001"
GLOBEX_POSTING = "7000000002"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)  # 2 days after GITLAB_POSTING's first_published
OPUS = "claude-opus-5-20260201"  # the newest Opus in models_list.json
SPEND_LIMIT = json.loads((FIXTURES / "anthropic/spend_limit_400.json").read_text())

ELIGIBLE = {
    "track": "backend",
    "verdict": "eligible",
    "reason": "Remota e aberta ao Brasil.",
    "remote": True,
    "open_to_brazil": True,
    "requires_us_work_authorization": False,
    "fit_score": 7,  # below auto_resume_min_fit_score: no Tailored resume unless a test asks
    "fit_reason": "Boa aderência ao perfil.",
    "job_language": "en",
}
BEST_MATCH = {**ELIGIBLE, "fit_score": 8}
# A Tailored resume identical to the Base resume invents nothing.
TAILORED = json.loads(Path("private.example/resume.json").read_text())
OPUS_6 = {
    "type": "model",
    "id": "claude-opus-6-20260801",
    "display_name": "Claude Opus 6",
    "created_at": "2026-08-01T00:00:00Z",
}


def _message(*, stop_reason: str = "end_turn", content: list[dict]) -> dict[str, Any]:
    return {
        "id": "msg_01abc",
        "type": "message",
        "role": "assistant",
        "model": OPUS,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def _succeeded(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload)
    return {"type": "succeeded", "message": _message(content=[{"type": "text", "text": text}])}


def _refused() -> dict[str, Any]:
    return {"type": "succeeded", "message": _message(stop_reason="refusal", content=[])}


class _Fakes:
    """MockTransport handlers for one test, with knobs the test turns between Runs."""

    def __init__(self) -> None:
        self.boards: dict[str, dict[str, Any] | int] = {"gitlab": GITLAB}  # body or HTTP status
        self.timeouts: set[str] = set()  # Board slugs whose fetch times out
        self.models = json.loads((FIXTURES / "anthropic/models_list.json").read_text())
        self.spend_limit: set[str] = set()  # "messages" and/or "batches"
        self.discovery_down = False  # /v1/messages answers 500
        self.telegram_down = False  # sendMessage answers 502
        self.processing = False  # every batch still in progress
        self.result = _succeeded(ELIGIBLE)  # each judge request's result...
        self.judgments: dict[str, dict[str, Any]] = {}  # ...unless its company is here
        self.resume = _succeeded(TAILORED)  # each resume request's result
        self.batches: dict[str, list[dict[str, Any]]] = {}  # batch id -> its requests
        self.calls: list[str] = []  # "METHOD path" of every Anthropic call
        self.sent: list[str] = []  # Telegram texts

    def ats(self, request: httpx2.Request) -> httpx2.Response:
        slug = request.url.path.split("/")[3]  # /v1/boards/<slug>/jobs
        if slug in self.timeouts:
            raise httpx2.ReadTimeout("timed out", request=request)
        board = self.boards[slug]
        if isinstance(board, int):
            return httpx2.Response(board)
        return httpx2.Response(200, json=board)

    def anthropic(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path == "/v1/models":
            return httpx2.Response(200, json=self.models)
        if path == "/v1/messages":
            if "messages" in self.spend_limit:
                return httpx2.Response(400, json=SPEND_LIMIT)
            if self.discovery_down:
                error = {"type": "api_error", "message": "Internal server error"}
                return httpx2.Response(500, json={"type": "error", "error": error})
            empty = json.loads((FIXTURES / "anthropic/discovery_empty.json").read_text())
            return httpx2.Response(200, json=empty)
        if path == "/v1/messages/batches" and request.method == "POST":
            if "batches" in self.spend_limit:
                return httpx2.Response(400, json=SPEND_LIMIT)
            batch_id = f"msgbatch_{len(self.batches) + 1}"
            self.batches[batch_id] = json.loads(request.content)["requests"]
            return httpx2.Response(200, json=self._batch(batch_id, ended=False))
        batch_id = path.split("/")[4]  # /v1/messages/batches/<id>[/results]
        if path.endswith("/results"):
            lines = [
                json.dumps({"custom_id": r["custom_id"], "result": self._result(r)})
                for r in self.batches[batch_id]
            ]
            return httpx2.Response(200, content="\n".join(lines).encode())
        return httpx2.Response(200, json=self._batch(batch_id, ended=not self.processing))

    def telegram(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/bottest-token/sendMessage"
        if self.telegram_down:
            return httpx2.Response(502)
        self.sent.append(json.loads(request.content)["text"])
        ok = json.loads((FIXTURES / "telegram/sendMessage-ok.json").read_text())
        return httpx2.Response(200, json=ok)

    def _result(self, request: dict[str, Any]) -> dict[str, Any]:
        if request["custom_id"].startswith("resume-"):
            return self.resume
        first_line = request["params"]["messages"][0]["content"].split("\n")[0]
        return self.judgments.get(first_line.removeprefix("Company: "), self.result)

    def _batch(self, batch_id: str, *, ended: bool) -> dict[str, Any]:
        return {
            "id": batch_id,
            "type": "message_batch",
            "processing_status": "ended" if ended else "in_progress",
            "request_counts": {
                "processing": 0 if ended else len(self.batches[batch_id]),
                "succeeded": len(self.batches[batch_id]) if ended else 0,
                "errored": 0,
                "canceled": 0,
                "expired": 0,
            },
            "created_at": "2026-08-05T12:00:00Z",
            "expires_at": "2026-08-06T12:00:00Z",
            "archived_at": None,
            "cancel_initiated_at": None,
            "ended_at": "2026-08-05T12:10:00Z" if ended else None,
            "results_url": f"/v1/messages/batches/{batch_id}/results" if ended else None,
        }


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")


@pytest.fixture
def fakes() -> _Fakes:
    return _Fakes()


@pytest.fixture
def ctx(tmp_path: Path, fakes: _Fakes) -> Iterator[RunContext]:
    """A RunContext over a temporary private dir (a copy of private.example/)
    with the GitLab Board already added."""
    private = tmp_path / "private"
    shutil.copytree("private.example", private)
    paths = private_paths({"TRAMPO_PRIVATE_DIR": str(private)})
    store = Store(paths.db)
    store.add_board(BoardRef(ats="greenhouse", slug="gitlab"), "GitLab", NOW)
    ats_http = httpx2.Client(transport=httpx2.MockTransport(fakes.ats))
    claude_http = httpx2.Client(transport=httpx2.MockTransport(fakes.anthropic))
    telegram_http = httpx2.Client(transport=httpx2.MockTransport(fakes.telegram))
    context = RunContext(
        store=store,
        config=load_config(),
        profile=load_profile(paths.profile_toml),
        paths=paths,
        claude=make_client(claude_http).with_options(max_retries=0),
        ats_clients={ats: client_for(ats, ats_http) for ats in ("greenhouse", "lever", "ashby")},
        telegram=Telegram(telegram_http, "test-token", "42"),
        now=lambda: NOW,
        sleep=lambda seconds: None,
    )
    yield context
    store.close()


def _add_board(
    ctx: RunContext, fakes: _Fakes, slug: str, company: str, posting_id: str, title: str
) -> None:
    """Another Greenhouse Board: GITLAB's backend Posting under another id,
    title and company, so it becomes its own Job."""
    ctx.store.add_board(BoardRef(ats="greenhouse", slug=slug), company, NOW)
    job = copy.deepcopy(GITLAB["jobs"][0])
    job.update(
        id=int(posting_id),
        title=title,
        absolute_url=f"https://job-boards.greenhouse.io/{slug}/jobs/{posting_id}",
    )
    fakes.boards[slug] = {"jobs": [job], "meta": {"total": 1}}


def _add_acme(ctx: RunContext, fakes: _Fakes) -> None:
    _add_board(ctx, fakes, "acme", "Acme", ACME_POSTING, "Senior Golang Engineer")


def _job_id(ctx: RunContext, posting_id: str) -> int:
    job_id = ctx.store.posting_job_id("greenhouse", posting_id)
    assert job_id is not None
    return job_id


def _row(ctx: RunContext, sql: str, *params: object) -> sqlite3.Row:
    conn = sqlite3.connect(ctx.paths.db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(sql, params).fetchone()
        assert row is not None
        return row
    finally:
        conn.close()


class _ProcessDied(BaseException):
    """The PC was turned off while polling."""


def _die(seconds: float) -> None:
    raise _ProcessDied


def _judged_ids(fakes: _Fakes) -> list[list[str]]:
    return [[r["custom_id"] for r in requests] for requests in fakes.batches.values()]


def test_happy_path_sends_digest(ctx: RunContext, fakes: _Fakes) -> None:
    summary = run(ctx)

    job_id = _job_id(ctx, GITLAB_POSTING)
    job = ctx.store.job(job_id).job
    assert summary == RunSummary(run_id=summary.run_id, new_jobs=1, eligible=1)
    assert _judged_ids(fakes) == [[f"judge-{job_id}"]]
    [request] = next(iter(fakes.batches.values()))
    assert request["params"]["model"] == OPUS
    assert job.verdict == "eligible"
    assert job.title == GITLAB_TITLE
    assert fakes.sent == format_digest([job], needs_review=0, rejected=0)
    assert GITLAB_TITLE in fakes.sent[0]

    judged = _row(ctx, "SELECT judged_model_id, judged_prompt_hash FROM jobs WHERE id = ?", job_id)
    assert tuple(judged) == (OPUS, load_prompt("judge").hash)
    run_row = _row(ctx, "SELECT outcome, model_id, counts FROM runs WHERE id = ?", summary.run_id)
    assert (run_row["outcome"], run_row["model_id"]) == ("ok", OPUS)
    assert json.loads(run_row["counts"])["eligible"] == 1


def test_second_run_is_quiet(ctx: RunContext, fakes: _Fakes) -> None:
    run(ctx)
    sent_after_first = list(fakes.sent)

    second = run(ctx)

    assert (second.new_jobs, second.eligible, second.needs_review, second.rejected) == (0, 0, 0, 0)
    assert len(fakes.batches) == 1  # nothing new to judge -> no second batch
    assert fakes.sent == sent_after_first  # no Digest


def test_board_outage_does_not_close_postings(ctx: RunContext, fakes: _Fakes) -> None:
    _add_acme(ctx, fakes)
    run(ctx)

    fakes.timeouts.add("gitlab")
    fakes.boards["acme"] = 503
    summary = run(ctx)

    assert summary.board_errors == 2
    for posting_id in (GITLAB_POSTING, ACME_POSTING):
        posting = _row(ctx, "SELECT closed_at FROM postings WHERE posting_id = ?", posting_id)
        assert posting["closed_at"] is None
        assert ctx.store.job(_job_id(ctx, posting_id)).job.closed_at is None
    assert {board.slug for board, _ in ctx.store.active_boards()} == {"gitlab", "acme"}
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"


def test_spend_limit_keeps_pending_and_alerts_once(ctx: RunContext, fakes: _Fakes) -> None:
    _add_acme(ctx, fakes)
    fakes.spend_limit.add("batches")

    first = run(ctx)

    gitlab_job, acme_job = _job_id(ctx, GITLAB_POSTING), _job_id(ctx, ACME_POSTING)
    assert (first.new_jobs, first.pending, first.eligible) == (2, 2, 0)
    assert fakes.sent == [format_spend_cap_alert(2)]
    assert fakes.batches == {}
    assert ctx.store.open_batches("judge") == []
    for job_id in (gitlab_job, acme_job):
        assert ctx.store.job(job_id).job.verdict == "pending"
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", first.run_id)["outcome"]
    assert outcome == "ok"

    # The cap resets; meanwhile Acme's Posting was taken down. Ten days later the
    # GitLab Posting is outside the 7-day window, but it is a known pending Job.
    fakes.spend_limit.clear()
    fakes.boards["acme"] = {"jobs": [], "meta": {"total": 0}}
    later = NOW + timedelta(days=10)
    ctx.now = lambda: later

    second = run(ctx)

    assert _judged_ids(fakes) == [[f"judge-{gitlab_job}"]]
    assert ctx.store.job(gitlab_job).job.verdict == "eligible"
    closed = ctx.store.job(acme_job).job
    assert (closed.verdict, closed.verdict_reason) == ("rejected", "vaga encerrada")
    assert (second.eligible, second.rejected, second.pending) == (1, 0, 0)
    assert format_spend_cap_alert(2) not in fakes.sent[1:]


def test_in_flight_batch_is_collected_not_resubmitted(ctx: RunContext, fakes: _Fakes) -> None:
    ctx.sleep = _die
    with pytest.raises(_ProcessDied):
        run(ctx)
    job_id = _job_id(ctx, GITLAB_POSTING)
    [(batch_id, job_ids, model_id, _)] = ctx.store.open_batches("judge")
    assert (job_ids, model_id) == ([job_id], OPUS)

    # Next Run: the batch is still processing -> left alone, its Job not resubmitted.
    ctx.sleep = lambda seconds: None
    fakes.processing = True
    waiting = run(ctx)

    assert list(fakes.batches) == [batch_id]
    assert ctx.store.job(job_id).job.verdict == "pending"
    assert (waiting.pending, fakes.sent) == (1, [])

    # The Run after: the batch has ended -> collected with the model it was
    # submitted with, even though a newer Opus exists now; still never resubmitted.
    fakes.processing = False
    fakes.models["data"].append(OPUS_6)
    collected = run(ctx)

    assert list(fakes.batches) == [batch_id]
    assert ctx.store.open_batches("judge") == []
    job = ctx.store.job(job_id).job
    assert job.verdict == "eligible"
    judged_model = _row(ctx, "SELECT judged_model_id FROM jobs WHERE id = ?", job_id)[0]
    assert judged_model == OPUS
    assert collected.eligible == 1
    assert fakes.sent == format_digest([job], needs_review=0, rejected=0)


def test_refusal_becomes_needs_review(ctx: RunContext, fakes: _Fakes) -> None:
    fakes.result = _refused()

    summary = run(ctx)

    job = ctx.store.job(_job_id(ctx, GITLAB_POSTING)).job
    assert (job.verdict, job.verdict_reason) == ("needs_review", "análise recusada pelo modelo")
    assert (summary.needs_review, summary.eligible, summary.pending) == (1, 0, 0)
    assert fakes.sent == []  # no eligible Job -> no Digest
    assert ctx.store.open_batches("judge") == []
    judged = _row(ctx, "SELECT judged_model_id, judged_prompt_hash FROM jobs WHERE id = ?", job.id)
    assert tuple(judged) == (OPUS, load_prompt("judge").hash)  # a refusal is a Verdict too


def test_deleted_board_closes_its_postings(ctx: RunContext, fakes: _Fakes) -> None:
    _add_acme(ctx, fakes)
    fakes.spend_limit.add("batches")  # both Jobs stay pending after Run 1
    run(ctx)

    fakes.spend_limit.clear()
    fakes.boards["acme"] = 404
    summary = run(ctx)

    acme_job = ctx.store.job(_job_id(ctx, ACME_POSTING)).job
    posting = _row(ctx, "SELECT closed_at FROM postings WHERE posting_id = ?", ACME_POSTING)
    assert posting["closed_at"] is not None
    assert acme_job.closed_at is not None
    assert (acme_job.verdict, acme_job.verdict_reason) == ("rejected", "vaga encerrada")
    assert [board.slug for board, _ in ctx.store.active_boards()] == ["gitlab"]
    assert _judged_ids(fakes) == [[f"judge-{_job_id(ctx, GITLAB_POSTING)}"]]
    assert summary.board_errors == 0  # a deleted Board is not an outage


def test_discovery_failure_does_not_stop_the_run(ctx: RunContext, fakes: _Fakes) -> None:
    fakes.discovery_down = True

    summary = run(ctx)

    assert "POST /v1/messages" in fakes.calls  # Discovery was tried and failed
    job = ctx.store.job(_job_id(ctx, GITLAB_POSTING)).job
    assert job.verdict == "eligible"
    assert fakes.sent == format_digest([job], needs_review=0, rejected=0)
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"


def test_unexpected_error_marks_run_failed_and_alerts(ctx: RunContext, fakes: _Fakes) -> None:
    fakes.models["data"] = [m for m in fakes.models["data"] if "opus" not in m["id"]]

    with pytest.raises(RuntimeError):
        run(ctx)

    error = "RuntimeError: No claude-opus-* model available to this API key"
    row = _row(ctx, "SELECT outcome, error, finished_at FROM runs ORDER BY id DESC")
    assert (row["outcome"], row["error"]) == ("failed", error)
    assert row["finished_at"] is not None
    assert fakes.sent == [format_failure(error)]


def test_digest_includes_previously_pending_job(ctx: RunContext, fakes: _Fakes) -> None:
    fakes.spend_limit.add("batches")
    run(ctx)
    gitlab_job = _job_id(ctx, GITLAB_POSTING)
    assert ctx.store.job(gitlab_job).job.verdict == "pending"

    fakes.spend_limit.clear()
    _add_acme(ctx, fakes)
    second = run(ctx)

    acme_job = _job_id(ctx, ACME_POSTING)
    jobs = [ctx.store.job(gitlab_job).job, ctx.store.job(acme_job).job]
    assert ctx.store.job(gitlab_job).job.created_run_id != second.run_id
    assert (second.new_jobs, second.eligible) == (1, 2)
    assert fakes.sent[1:] == format_digest(jobs, needs_review=0, rejected=0)


def test_spend_limit_in_discovery_skips_judging_and_alerts_once(
    ctx: RunContext, fakes: _Fakes
) -> None:
    fakes.spend_limit.update({"messages", "batches"})

    summary = run(ctx)

    assert fakes.calls.count("POST /v1/messages") == 1  # Discovery stops at the first error
    assert "POST /v1/messages/batches" not in fakes.calls  # judging skipped
    assert fakes.sent == [format_spend_cap_alert(1)]
    assert (summary.new_jobs, summary.pending) == (1, 1)
    assert ctx.store.job(_job_id(ctx, GITLAB_POSTING)).job.verdict == "pending"
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"


def test_late_judgment_never_overrides_a_closed_job(ctx: RunContext, fakes: _Fakes) -> None:
    ctx.sleep = _die
    with pytest.raises(_ProcessDied):
        run(ctx)
    job_id = _job_id(ctx, GITLAB_POSTING)

    # The batch is still processing while the Posting vanishes -> Job closed + rejected.
    ctx.sleep = lambda seconds: None
    fakes.processing = True
    fakes.boards["gitlab"] = {"jobs": [], "meta": {"total": 0}}
    run(ctx)
    assert ctx.store.job(job_id).job.verdict == "rejected"

    # The batch ends: its eligible Judgment arrives too late and is dropped.
    fakes.processing = False
    late = run(ctx)

    job = ctx.store.job(job_id).job
    assert (job.verdict, job.verdict_reason) == ("rejected", "vaga encerrada")
    assert ctx.store.open_batches("judge") == []  # collected all the same
    assert late.eligible == 0
    assert fakes.sent == []


def test_digest_skips_jobs_closed_after_recovery(ctx: RunContext, fakes: _Fakes) -> None:
    _add_acme(ctx, fakes)
    ctx.sleep = _die
    with pytest.raises(_ProcessDied):
        run(ctx)

    # Step 2 recovers both Jobs as eligible; then Acme's Posting is gone, so
    # step 6 closes its Job: the Digest must not advertise a dead link.
    ctx.sleep = lambda seconds: None
    fakes.boards["acme"] = {"jobs": [], "meta": {"total": 0}}
    run(ctx)

    gitlab = ctx.store.job(_job_id(ctx, GITLAB_POSTING)).job
    acme = ctx.store.job(_job_id(ctx, ACME_POSTING)).job
    assert (acme.verdict, acme.closed_at is not None) == ("eligible", True)
    assert fakes.sent == format_digest([gitlab], needs_review=0, rejected=0)


def test_start_run_failure_alerts_and_reraises(ctx: RunContext, fakes: _Fakes) -> None:
    ctx.store.close()  # stands in for a locked or broken database

    with pytest.raises(sqlite3.ProgrammingError):
        run(ctx)

    assert fakes.sent == [format_failure("ProgrammingError: Cannot operate on a closed database.")]
    assert fakes.calls == []  # nothing else ran


def test_spend_cap_alert_failure_does_not_stop_the_run(ctx: RunContext, fakes: _Fakes) -> None:
    fakes.spend_limit.add("batches")
    fakes.telegram_down = True

    summary = run(ctx)

    assert summary.pending == 1
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"


def test_digest_send_failure_does_not_stop_the_run(
    ctx: RunContext, fakes: _Fakes, tmp_path: Path
) -> None:
    fakes.result = _succeeded(BEST_MATCH)
    fakes.telegram_down = True
    ctx.profile.backup_dir = tmp_path / "backup"

    summary = run(ctx)

    job = ctx.store.job(_job_id(ctx, GITLAB_POSTING)).job
    assert summary.eligible == 1
    assert len(fakes.batches) == 2  # the resume batch still went out...
    assert job.resume_path is not None
    assert (tmp_path / "backup" / "trampo.db").exists()  # ...and the backup ran
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"


def test_discarded_jobs_are_dismissed_and_left_out_of_the_digest(
    ctx: RunContext, fakes: _Fakes
) -> None:
    _add_acme(ctx, fakes)
    _add_board(ctx, fakes, "globex", "Globex", GLOBEX_POSTING, "Senior Backend Engineer")
    fakes.judgments = {  # GitLab: eligible at Fit score 7
        "Acme": _succeeded({**ELIGIBLE, "verdict": "rejected", "reason": "Só EUA."}),
        "Globex": _succeeded({**ELIGIBLE, "fit_score": 4}),
    }

    summary = run(ctx)

    gitlab, acme, globex = (
        ctx.store.job(_job_id(ctx, p)).job for p in (GITLAB_POSTING, ACME_POSTING, GLOBEX_POSTING)
    )
    assert (gitlab.status, acme.status, globex.status) == ("new", "dismissed", "dismissed")
    assert summary.dismissed == 2
    assert fakes.sent == format_digest([gitlab], needs_review=0, rejected=1)


def test_auto_only_for_eligible_at_threshold(ctx: RunContext, fakes: _Fakes) -> None:
    _add_acme(ctx, fakes)
    _add_board(ctx, fakes, "globex", "Globex", GLOBEX_POSTING, "Senior Backend Engineer")
    fakes.judgments = {  # GitLab: eligible at Fit score 7
        "Acme": _succeeded({**BEST_MATCH, "verdict": "needs_review", "fit_score": 9}),
        "Globex": _succeeded(BEST_MATCH),
    }

    summary = run(ctx)

    gitlab, acme, globex = (_job_id(ctx, p) for p in (GITLAB_POSTING, ACME_POSTING, GLOBEX_POSTING))
    assert (summary.eligible, summary.needs_review) == (2, 1)
    [judge_batch, resume_batch] = _judged_ids(fakes)
    assert len(judge_batch) == 3
    assert resume_batch == [f"resume-{globex}"]
    [request] = list(fakes.batches.values())[1]
    assert request["params"]["model"] == OPUS
    assert "Go, distributed systems" in request["params"]["messages"][0]["content"]

    path = ctx.paths.resumes_dir / "globex-senior-backend-engineer.pdf"
    assert ctx.store.job(globex).job.resume_path == str(path)
    assert path.read_bytes().startswith(b"%PDF")
    stored = _row(ctx, "SELECT resume_model_id, resume_prompt_hash FROM jobs WHERE id = ?", globex)
    assert tuple(stored) == (OPUS, load_prompt("tailor").hash)
    for job_id in (gitlab, acme):
        assert ctx.store.job(job_id).job.resume_path is None
    assert ctx.store.open_batches("resume") == []


def test_refused_resume_not_saved(
    ctx: RunContext, fakes: _Fakes, caplog: pytest.LogCaptureFixture
) -> None:
    fakes.result = _succeeded(BEST_MATCH)
    invented = copy.deepcopy(TAILORED)
    invented["work"][0]["name"] = "Globex"
    fakes.resume = _succeeded(invented)

    summary = run(ctx)

    job = ctx.store.job(_job_id(ctx, GITLAB_POSTING)).job
    assert job.resume_path is None
    assert not ctx.paths.resumes_dir.exists()  # nothing rendered
    assert "Globex" in caplog.text  # the flagged fact is logged
    assert ctx.store.open_batches("resume") == []  # collected all the same
    assert fakes.sent == format_digest([job], needs_review=0, rejected=0)
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"


def test_resume_batch_recovered_next_run(ctx: RunContext, fakes: _Fakes) -> None:
    fakes.result = _succeeded(BEST_MATCH)
    # The PC is turned off while polling the second (resume) batch, after the Digest.
    ctx.sleep = lambda seconds: _die(seconds) if len(fakes.batches) == 2 else None
    with pytest.raises(_ProcessDied):
        run(ctx)
    job_id = _job_id(ctx, GITLAB_POSTING)
    [(_, job_ids, model_id, _)] = ctx.store.open_batches("resume")
    assert (job_ids, model_id) == ([job_id], OPUS)
    assert ctx.store.job(job_id).job.resume_path is None
    assert len(fakes.sent) == 1  # the Digest went out before the resume batch

    # Next Run: collected with the model and prompt it was submitted with, even
    # though a newer Opus exists now; never resubmitted.
    ctx.sleep = lambda seconds: None
    fakes.models["data"].append(OPUS_6)
    run(ctx)

    assert len(fakes.batches) == 2
    assert ctx.store.open_batches("resume") == []
    resume_path = ctx.store.job(job_id).job.resume_path
    assert resume_path is not None
    assert Path(resume_path).read_bytes().startswith(b"%PDF")
    stored = _row(ctx, "SELECT resume_model_id, resume_prompt_hash FROM jobs WHERE id = ?", job_id)
    assert tuple(stored) == (OPUS, load_prompt("tailor").hash)


def test_spend_limit_on_resume_batch_alerts_once(ctx: RunContext, fakes: _Fakes) -> None:
    fakes.result = _succeeded(BEST_MATCH)
    # The cap is reached while the judge batch is polled: the resume batch is refused.
    ctx.sleep = lambda seconds: fakes.spend_limit.add("batches")

    summary = run(ctx)

    job = ctx.store.job(_job_id(ctx, GITLAB_POSTING)).job
    assert (job.verdict, job.resume_path) == ("eligible", None)
    assert list(fakes.batches) == ["msgbatch_1"]  # the judge batch only
    assert ctx.store.open_batches("resume") == []
    digest = format_digest([job], needs_review=0, rejected=0)
    assert fakes.sent == [*digest, format_spend_cap_alert(0)]
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"


def test_resume_save_failure_skips_only_that_job(
    ctx: RunContext,
    fakes: _Fakes,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _add_acme(ctx, fakes)
    fakes.result = _succeeded(BEST_MATCH)

    def html_to_pdf(html: str, out: Path) -> None:  # no Chromium: fails for Acme only
        if out.name.startswith("acme-"):
            raise RuntimeError("Chromium is not installed")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"%PDF-stub")

    monkeypatch.setattr(tailor, "html_to_pdf", html_to_pdf)

    summary = run(ctx)

    gitlab = ctx.store.job(_job_id(ctx, GITLAB_POSTING)).job
    acme = ctx.store.job(_job_id(ctx, ACME_POSTING)).job
    assert gitlab.resume_path is not None
    assert Path(gitlab.resume_path).read_bytes() == b"%PDF-stub"
    assert acme.resume_path is None  # on demand stays possible
    assert "Chromium is not installed" in caplog.text
    assert ctx.store.open_batches("resume") == []  # collected: no failing retry every Run
    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"


def test_recovered_resume_never_overwrites_one_made_by_hand(ctx: RunContext, fakes: _Fakes) -> None:
    fakes.result = _succeeded(BEST_MATCH)
    ctx.sleep = lambda seconds: _die(seconds) if len(fakes.batches) == 2 else None
    with pytest.raises(_ProcessDied):
        run(ctx)
    job_id = _job_id(ctx, GITLAB_POSTING)
    # Meanwhile the Candidate generated one on demand.
    by_hand = ctx.paths.resumes_dir / "by-hand.pdf"
    ctx.store.save_resume(job_id, by_hand, "claude-opus-by-hand", "hand0000hash")

    ctx.sleep = lambda seconds: None
    run(ctx)

    assert ctx.store.open_batches("resume") == []  # collected all the same
    stored = _row(
        ctx,
        "SELECT resume_path, resume_model_id, resume_prompt_hash FROM jobs WHERE id = ?",
        job_id,
    )
    assert tuple(stored) == (str(by_hand), "claude-opus-by-hand", "hand0000hash")
    assert not ctx.paths.resumes_dir.exists()  # nothing rendered over it


def test_skipped_without_backup_dir(ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ctx.profile.backup_dir is None  # private.example/profile.toml has dir = ""
    calls: list[object] = []
    monkeypatch.setattr(pipeline, "backup_private", lambda *args: calls.append(args))

    pipeline._backup(ctx)

    assert calls == []


def test_failure_does_not_fail_run(ctx: RunContext, fakes: _Fakes, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    ctx.profile.backup_dir = blocker / "backup"  # mkdir under a regular file fails

    summary = run(ctx)

    outcome = _row(ctx, "SELECT outcome FROM runs WHERE id = ?", summary.run_id)["outcome"]
    assert outcome == "ok"
