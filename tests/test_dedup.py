from datetime import UTC, datetime, timedelta
from pathlib import Path

from trampo.dedup import assign_job, normalize_title
from trampo.models import Posting
from trampo.store import Store

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
REPOST_DAYS = 30


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


def test_location_variants_normalize_equal() -> None:
    a = normalize_title("Senior Backend Engineer (Remote - LATAM)")
    b = normalize_title("Senior Backend Engineer - Brazil")
    c = normalize_title("senior backend engineer")
    assert a == b
    assert b == c


def test_team_suffix_kept() -> None:
    payments = normalize_title("Senior Engineer - Payments")
    platform = normalize_title("Senior Engineer - Platform")
    assert payments != platform


def test_same_run_locations_share_job(tmp_path: Path) -> None:
    db_path = tmp_path / "trampo.db"
    store = Store(db_path)
    run_id = store.start_run(NOW)
    p1 = _posting(posting_id="p1", location="Remote - Brazil")
    p2 = _posting(posting_id="p2", location="Remote - Argentina")

    job_id_1, created_1 = assign_job(store, p1, run_id, NOW, REPOST_DAYS)
    store.upsert_posting(p1, job_id_1, NOW)
    job_id_2, created_2 = assign_job(store, p2, run_id, NOW, REPOST_DAYS)
    store.upsert_posting(p2, job_id_2, NOW)
    store.close()

    assert created_1 is True
    assert created_2 is False
    assert job_id_1 == job_id_2


def test_repost_within_window_keeps_job_and_status(tmp_path: Path) -> None:
    db_path = tmp_path / "trampo.db"
    store = Store(db_path)
    run_id = store.start_run(NOW)
    old_posting = _posting(posting_id="p1")
    job_id, _ = assign_job(store, old_posting, run_id, NOW, REPOST_DAYS)
    store.upsert_posting(old_posting, job_id, NOW)
    store.set_status(job_id, "applied")

    later = NOW + timedelta(days=20)
    new_posting = _posting(posting_id="p2")
    new_job_id, created_again = assign_job(store, new_posting, run_id, later, REPOST_DAYS)
    status = store.job(job_id).job.status
    store.close()

    assert created_again is False
    assert new_job_id == job_id
    assert status == "applied"


def test_repost_after_window_creates_new_job(tmp_path: Path) -> None:
    db_path = tmp_path / "trampo.db"
    store = Store(db_path)
    run_id = store.start_run(NOW)
    old_posting = _posting(posting_id="p1")
    job_id, _ = assign_job(store, old_posting, run_id, NOW, REPOST_DAYS)
    store.upsert_posting(old_posting, job_id, NOW)

    later = NOW + timedelta(days=40)
    new_posting = _posting(posting_id="p2")
    new_job_id, created = assign_job(store, new_posting, run_id, later, REPOST_DAYS)
    store.close()

    assert created is True
    assert new_job_id != job_id
