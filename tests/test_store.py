import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trampo.models import BoardRef, Judgment, Posting
from trampo.store import Store

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _posting(**overrides: object) -> Posting:
    defaults: dict[str, object] = {
        "ats": "greenhouse",
        "board_slug": "acme",
        "posting_id": "p1",
        "company": "Acme",
        "title": "Backend Engineer",
        "location": "Remote",
        "url": "https://acme.example/p1",
        "description": "Great job.",
        "workplace": "remote",
        "salary": None,
        "published_at": None,
    }
    defaults.update(overrides)
    return Posting.model_validate(defaults)


def _select_posting(path: Path, ats: str, posting_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM postings WHERE ats = ? AND posting_id = ?", (ats, posting_id)
        ).fetchone()
        assert row is not None
        return row
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "trampo.db"


def test_schema_applied_once(db_path: Path) -> None:
    board = BoardRef(ats="greenhouse", slug="acme")

    store1 = Store(db_path)
    store1.add_board(board, "Acme", NOW)
    store1.close()

    store2 = Store(db_path)
    assert store2.active_boards() == [(board, "Acme")]
    store2.close()

    conn = sqlite3.connect(db_path)
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    conn.close()
    assert version == 1


def test_upsert_posting_keeps_first_seen(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    job_id = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)
    posting = _posting()

    store.upsert_posting(posting, job_id, NOW)
    later = NOW + timedelta(days=1)
    store.upsert_posting(posting, job_id, later)
    store.close()

    row = _select_posting(db_path, "greenhouse", "p1")
    assert row["first_seen_at"] == NOW.isoformat()
    assert row["last_seen_at"] == later.isoformat()
    assert row["job_id"] == job_id


def test_close_missing_postings_only_touches_that_board(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    board_a = BoardRef(ats="greenhouse", slug="acme")

    job_a1 = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)
    job_a2 = store.create_job("Acme", "Frontend Engineer", "frontend engineer", run_id, NOW)
    job_b = store.create_job("Other", "Backend Engineer", "backend engineer", run_id, NOW)

    store.upsert_posting(_posting(board_slug="acme", posting_id="a1"), job_a1, NOW)
    store.upsert_posting(
        _posting(board_slug="acme", posting_id="a2", title="Frontend Engineer"), job_a2, NOW
    )
    store.upsert_posting(_posting(board_slug="other", posting_id="b1", company="Other"), job_b, NOW)

    # a2 no longer appears on board_a's listing; b1 (a different board) is untouched
    store.close_missing_postings(board_a, {"a1"}, NOW)
    store.close()

    assert _select_posting(db_path, "greenhouse", "a1")["closed_at"] is None
    assert _select_posting(db_path, "greenhouse", "a2")["closed_at"] == NOW.isoformat()
    assert _select_posting(db_path, "greenhouse", "b1")["closed_at"] is None


def test_upsert_reopens_closed_job(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    job_id = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)
    posting = _posting()
    store.upsert_posting(posting, job_id, NOW)

    store.close_missing_postings(BoardRef(ats="greenhouse", slug="acme"), set(), NOW)
    closed_ids = store.close_jobs_without_open_postings(NOW)
    assert closed_ids == [job_id]
    assert store.job(job_id).job.closed_at is not None

    later = NOW + timedelta(days=1)
    store.upsert_posting(posting, job_id, later)  # Repost reappears
    result = store.job(job_id)
    store.close()

    assert result.job.closed_at is None


def test_find_recent_job_respects_since(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    old_created = NOW - timedelta(days=40)
    job_id = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, old_created)
    posting = _posting()
    store.upsert_posting(posting, job_id, old_created)

    since = NOW - timedelta(days=30)
    assert store.find_recent_job("Acme", "backend engineer", since) is None

    recent = NOW - timedelta(days=1)
    store.upsert_posting(posting, job_id, recent)  # bumps last activity within the window
    found = store.find_recent_job("Acme", "backend engineer", since)
    store.close()

    assert found == job_id


def test_find_recent_job_normalizes_non_utc_offsets(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    job_id = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)
    posting = _posting()

    # The posting's real last-activity instant is 1 hour AFTER `since`, so it must be
    # found -- but it's passed in a negative, non-UTC offset (BRT, UTC-3) whose local
    # wall-clock hour is smaller than `since`'s UTC hour. A naive isoformat() stored
    # verbatim (keeping "-03:00") would TEXT-sort as earlier than `since` and be wrongly
    # excluded; storage must normalize to UTC first.
    since = NOW
    brt = timezone(timedelta(hours=-3))
    last_activity_brt = (NOW + timedelta(hours=1)).astimezone(brt)
    store.upsert_posting(posting, job_id, last_activity_brt)

    found = store.find_recent_job("Acme", "backend engineer", since)
    store.close()

    assert found == job_id


def test_jobs_to_judge_skips_jobs_in_open_batches(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    pending_job = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)
    batched_job = store.create_job("Acme", "SRE", "sre", run_id, NOW)
    closed_job = store.create_job("Acme", "Data Engineer", "data engineer", run_id, NOW)

    store.add_batch("batch-1", "judge", run_id, [batched_job], NOW)

    # a closed job keeps verdict=pending but must not be sent to judging
    store.upsert_posting(_posting(board_slug="acme", posting_id="closed-1"), closed_job, NOW)
    store.close_missing_postings(BoardRef(ats="greenhouse", slug="acme"), set(), NOW)
    store.close_jobs_without_open_postings(NOW)

    to_judge = {jwp.job.id for jwp in store.jobs_to_judge()}
    store.close()

    assert to_judge == {pending_job}


def test_override_records_from_and_to(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    job_id = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)
    store.set_verdict(job_id, "needs_review", "unclear location")

    store.override_verdict(job_id, "eligible", "candidate confirmed remote", NOW)
    updated = store.job(job_id).job
    store.close()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    override_row = conn.execute("SELECT * FROM overrides WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()

    assert override_row["from_verdict"] == "needs_review"
    assert override_row["to_verdict"] == "eligible"
    assert updated.verdict == "eligible"
    assert updated.verdict_reason == "candidate confirmed remote"


def test_list_jobs_filters(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    applied_job = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)
    judged_job = store.create_job("Acme", "SRE", "sre", run_id, NOW)
    other_company_job = store.create_job(
        "Other Co", "Backend Engineer", "backend engineer", run_id, NOW
    )
    store.set_status(applied_job, "applied")
    store.save_judgment(
        judged_job,
        Judgment(
            track="backend",
            verdict="eligible",
            reason="ok",
            remote=True,
            open_to_brazil=True,
            requires_us_work_authorization=False,
            fit_score=9,
            fit_reason="great fit",
            job_language="en",
        ),
        "model-x",
        "abcdef123456",
    )

    by_status = {j.id for j in store.list_jobs(status="applied")}
    by_track = {j.id for j in store.list_jobs(track="backend")}
    by_company = {j.id for j in store.list_jobs(company="Other Co")}
    store.close()

    assert by_status == {applied_job}
    assert by_track == {judged_job}
    assert by_company == {other_company_job}


def test_reject_closed_pending(db_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    pending_closed = store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)
    eligible_closed = store.create_job("Acme", "SRE", "sre", run_id, NOW)
    pending_open = store.create_job("Acme", "Data Engineer", "data engineer", run_id, NOW)
    store.set_verdict(eligible_closed, "eligible", "great fit")

    store.upsert_posting(_posting(board_slug="acme", posting_id="c1"), pending_closed, NOW)
    store.upsert_posting(_posting(board_slug="acme", posting_id="c2"), eligible_closed, NOW)
    store.close_missing_postings(BoardRef(ats="greenhouse", slug="acme"), set(), NOW)
    store.close_jobs_without_open_postings(NOW)

    count = store.reject_closed_pending("posting closed before judged")
    verdicts = {j.id: j.verdict for j in store.list_jobs()}
    store.close()

    assert count == 1
    assert verdicts[pending_closed] == "rejected"
    assert verdicts[eligible_closed] == "eligible"
    assert verdicts[pending_open] == "pending"


def test_known_boards_includes_inactive(db_path: Path) -> None:
    store = Store(db_path)
    active = BoardRef(ats="greenhouse", slug="acme")
    inactive = BoardRef(ats="lever", slug="foo")
    store.add_board(active, "Acme", NOW)
    store.add_board(inactive, "Foo", NOW)
    store.deactivate_board(inactive)

    known = store.known_boards()
    store.close()

    assert known == {active, inactive}


def test_backup_to_while_open(db_path: Path, tmp_path: Path) -> None:
    store = Store(db_path)
    run_id = store.start_run(NOW)
    store.create_job("Acme", "Backend Engineer", "backend engineer", run_id, NOW)

    backup_path = tmp_path / "backup.db"
    store.backup_to(backup_path)
    store.close()

    backup_store = Store(backup_path)
    jobs = backup_store.list_jobs()
    backup_store.close()

    assert len(jobs) == 1
    assert jobs[0].company == "Acme"
