"""create_app tests: FastAPI TestClient against a real Store in tmp_path and a
fake `tailor`. No network, no private/."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from trampo.config import Paths
from trampo.models import Judgment, Posting, StatusValue, VerdictValue
from trampo.resume.tailor import InventedFactsError
from trampo.store import Store
from trampo.web import create_app

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _paths(tmp_path: Path) -> Paths:
    return Paths(
        private_dir=tmp_path,
        db=tmp_path / "trampo.db",
        resume_json=tmp_path / "resume.json",
        profile_toml=tmp_path / "profile.toml",
        resumes_dir=tmp_path / "resumes",
        logs_dir=tmp_path / "logs",
    )


def _posting(**overrides: object) -> Posting:
    defaults: dict[str, object] = {
        "ats": "greenhouse",
        "board_slug": "acme",
        "posting_id": "1",
        "company": "Acme",
        "title": "Backend Engineer",
        "location": "Remote",
        "url": "https://acme.example/1",
        "description": "Build things.",
        "workplace": "remote",
        "salary": None,
        "published_at": None,
    }
    defaults.update(overrides)
    return Posting.model_validate(defaults)


def _job(
    store: Store,
    *,
    company: str = "Acme",
    title: str = "Backend Engineer",
    status: StatusValue = "new",
    verdict: VerdictValue = "eligible",
    track: str = "backend",
    fit_score: int = 8,
    created_at: datetime = NOW,
) -> int:
    run_id = store.start_run(created_at)
    job_id = store.create_job(company, title, title.lower(), run_id, created_at)
    store.upsert_posting(
        _posting(company=company, title=title, url=f"https://acme.example/{job_id}"),
        job_id,
        created_at,
    )
    if verdict != "pending":
        store.save_judgment(
            job_id,
            Judgment(
                track=track,  # type: ignore[arg-type]
                verdict=verdict,  # type: ignore[arg-type]
                reason="bom encaixe",
                remote=True,
                open_to_brazil=True,
                requires_us_work_authorization=False,
                fit_score=fit_score,
                fit_reason="ótimo fit",
                job_language="pt",
            ),
            "model-x",
            "abcdef123456",
        )
    if status != "new":
        store.set_status(job_id, status)
    return job_id


def _app(store: Store, paths: Path | None = None, tmp_path: Path | None = None) -> TestClient:
    assert tmp_path is not None
    app = create_app(store, _paths(tmp_path), lambda job_id: Path("unused"), lambda: NOW)
    return TestClient(app)


def test_index_lists_jobs_in_portuguese(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")
    _job(store, company="Acme", title="Engenheiro Backend", verdict="eligible", fit_score=9)
    client = _app(store, tmp_path=tmp_path)

    resp = client.get("/")

    assert resp.status_code == 200
    assert "Acme" in resp.text
    assert "Engenheiro Backend" in resp.text
    assert "elegível" in resp.text
    assert "Notas" in resp.text
    assert "Gerar currículo" in resp.text


def test_filters(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")
    _job(store, company="Acme", title="Backend Engineer", status="new", track="backend")
    _job(store, company="Other Co", title="SRE", status="applied", track="agents")
    client = _app(store, tmp_path=tmp_path)

    by_status = client.get("/", params={"status": "applied"})
    assert "Other Co" in by_status.text
    assert "Acme" not in by_status.text

    by_track = client.get("/", params={"track": "agents"})
    assert "Other Co" in by_track.text
    assert "Acme" not in by_track.text

    by_company = client.get("/", params={"company": "Acme"})
    assert "Acme" in by_company.text
    assert "Other Co" not in by_company.text


def test_status_change_persists(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")
    job_id = _job(store, status="new")
    client = _app(store, tmp_path=tmp_path)

    resp = client.post(f"/jobs/{job_id}/status", data={"status": "seen"})

    assert resp.status_code == 200
    assert store.job(job_id).job.status == "seen"
    assert "vista" in resp.text


def test_override_creates_override_row(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")
    job_id = _job(store, verdict="rejected")
    client = _app(store, tmp_path=tmp_path)

    resp = client.post(
        f"/jobs/{job_id}/override",
        data={"verdict": "eligible", "reason": "candidato confirma remoto"},
    )

    assert resp.status_code == 200
    updated = store.job(job_id).job
    assert updated.verdict == "eligible"
    assert updated.verdict_reason == "candidato confirma remoto"
    [(_job_with_postings, to_verdict)] = store.overridden_jobs()
    assert to_verdict == "eligible"


def test_override_without_reason_refused(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")
    job_id = _job(store, verdict="rejected")
    client = _app(store, tmp_path=tmp_path)

    resp = client.post(f"/jobs/{job_id}/override", data={"verdict": "eligible", "reason": "   "})

    assert resp.status_code == 400
    assert store.job(job_id).job.verdict == "rejected"


def test_resume_invented_facts_shows_message(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")
    job_id = _job(store)

    def tailor(job_id: int) -> Path:
        raise InventedFactsError(["empresa não consta no currículo base: Globex"])

    app = create_app(store, _paths(tmp_path), tailor, lambda: NOW)
    client = TestClient(app)

    resp = client.post(f"/jobs/{job_id}/resume")

    assert resp.status_code == 200
    assert "recusad" in resp.text.lower()
    assert store.job(job_id).job.resume_path is None


def test_new_since_last_visit_highlighted_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")
    before = NOW - timedelta(hours=2)
    store.set_meta("last_visit", before.isoformat())
    created_at = NOW - timedelta(minutes=30)
    _job(store, created_at=created_at)
    client = _app(store, tmp_path=tmp_path)

    first = client.get("/")
    assert "Novidade" in first.text

    second = client.get("/")
    assert "Novidade" not in second.text


def test_resume_path_traversal_blocked(tmp_path: Path) -> None:
    store = Store(tmp_path / "trampo.db")
    paths = _paths(tmp_path)
    paths.resumes_dir.mkdir(parents=True)
    (paths.private_dir / "profile.toml").write_text("segredo = 1\n")
    app = create_app(store, paths, lambda job_id: Path("unused"), lambda: NOW)
    client = TestClient(app)

    resp = client.get("/resumes/..%2Fprofile.toml")

    assert resp.status_code == 404
