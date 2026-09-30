"""Local Job page: FastAPI + Jinja2 + htmx + Pico.css, UI in pt-BR, served on
127.0.0.1 only (CLAUDE.md, `trampo serve`).

Every route is a plain `def`, not `async def`: FastAPI runs it in its
threadpool, which keeps Playwright's sync API (used by `tailor`) off the
event loop. The Store's sqlite3 connection is opened with
`check_same_thread=False` (store.py) so it can be shared across those
worker threads.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast, get_args
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trampo.claude import SpendLimitReached
from trampo.config import Paths, TrackId
from trampo.digest import TRACK_LABELS
from trampo.models import JobRow, Salary, StatusValue, VerdictValue
from trampo.resume.tailor import InventedFactsError, TailorRefused
from trampo.store import JobNotFound, Store

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent
_TZ = ZoneInfo("America/Sao_Paulo")

_STATUS_LABELS: dict[str, str] = {
    "new": "nova",
    "seen": "vista",
    "applied": "candidatada",
    "dismissed": "descartada",
}
_VERDICT_LABELS: dict[str, str] = {
    "pending": "pendente",
    "eligible": "elegível",
    "needs_review": "revisar",
    "rejected": "rejeitada",
}
_WORKPLACE_LABELS = {"remote": "Remoto", "hybrid": "Híbrido", "onsite": "Presencial"}
_INTERVAL_LABELS = {"year": "ano", "month": "mês", "hour": "hora"}

_STATUS_VALUES = set(get_args(StatusValue))
_TRACK_VALUES = set(get_args(TrackId))


class MissingApiKey(Exception):
    """Gerar currículo needs ANTHROPIC_API_KEY even though `trampo serve` itself
    starts without one."""


def _label(mapping: dict[str, str]) -> Callable[[str | None], str]:
    def label(value: str | None) -> str:
        if value is None:
            return "—"
        return mapping.get(value, value)

    return label


def _br_date(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone(_TZ).strftime("%d/%m/%Y")


def _br_salary(salary: Salary | None) -> str | None:
    if salary is None:
        return None
    bounds = [f"{salary.currency} {v:.0f}" for v in (salary.min, salary.max) if v is not None]
    interval = _INTERVAL_LABELS.get(salary.interval, salary.interval)
    return f"{' – '.join(bounds)} / {interval}"


def _basename(path: str | None) -> str | None:
    return Path(path).name if path is not None else None


def _require_htmx(request: Request) -> None:
    """Every POST comes from the page's own htmx, which sends HX-Request: a
    plain form post from another origin can't add that header (CSRF)."""
    if request.method == "POST" and request.headers.get("HX-Request") != "true":
        raise HTTPException(403, "requisição recusada")


def create_app(
    store: Store, paths: Paths, tailor: Callable[[int], Path], now: Callable[[], datetime]
) -> FastAPI:
    app = FastAPI(dependencies=[Depends(_require_htmx)])
    # DNS rebinding: a page on another name resolving to 127.0.0.1 gets a 400.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.mount("/static", StaticFiles(directory=_PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=_PACKAGE_DIR / "templates")
    env = templates.env
    env.filters["status_label"] = _label(_STATUS_LABELS)
    env.filters["verdict_label"] = _label(_VERDICT_LABELS)
    env.filters["track_label"] = _label(TRACK_LABELS)
    env.filters["workplace_label"] = _label(_WORKPLACE_LABELS)
    env.filters["br_date"] = _br_date
    env.filters["br_salary"] = _br_salary
    env.filters["basename"] = _basename
    env.globals["status_options"] = list(_STATUS_LABELS.items())
    env.globals["track_options"] = list(TRACK_LABELS.items())
    # An Override sets a real Verdict; `pending` only means "not judged yet".
    env.globals["verdict_options"] = [
        (value, label) for value, label in _VERDICT_LABELS.items() if value != "pending"
    ]

    def _row(
        request: Request,
        job: JobRow,
        *,
        highlighted: bool,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "_job_row.html",
            {"job": job, "highlighted": highlighted, "error": error},
            status_code=status_code,
        )

    def _get_job(job_id: int) -> JobRow:
        try:
            return store.job(job_id).job
        except JobNotFound:
            raise HTTPException(404, "vaga não encontrada") from None

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        status: str = "",
        track: str = "",
        company: str = "",
        since: str = "",
    ) -> HTMLResponse:
        since_dt: datetime | None = None
        if since:
            try:
                since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=_TZ).astimezone(UTC)
            except ValueError:
                raise HTTPException(400, "data inválida") from None

        jobs = store.list_jobs(
            status=cast(StatusValue, status) if status in _STATUS_VALUES else None,
            track=cast(TrackId, track) if track in _TRACK_VALUES else None,
            company=company or None,
            since=since_dt,
        )
        last_visit_raw = store.get_meta("last_visit")
        last_visit = datetime.fromisoformat(last_visit_raw) if last_visit_raw else None
        rows = [(job, last_visit is None or job.created_at > last_visit) for job in jobs]
        store.set_meta("last_visit", now().isoformat())
        return templates.TemplateResponse(
            request,
            "index.html",
            {"rows": rows, "status": status, "track": track, "company": company, "since": since},
        )

    @app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
    def set_status(
        request: Request, job_id: int, status: Annotated[StatusValue, Form()]
    ) -> HTMLResponse:
        _get_job(job_id)
        store.set_status(job_id, status)
        return _row(request, store.job(job_id).job, highlighted=False)

    @app.post("/jobs/{job_id}/notes", response_class=HTMLResponse)
    def set_notes(request: Request, job_id: int, notes: Annotated[str, Form()]) -> HTMLResponse:
        _get_job(job_id)
        store.set_notes(job_id, notes)
        return _row(request, store.job(job_id).job, highlighted=False)

    @app.post("/jobs/{job_id}/override", response_class=HTMLResponse)
    def override(
        request: Request,
        job_id: int,
        verdict: Annotated[VerdictValue, Form()],
        reason: Annotated[str, Form()],
    ) -> HTMLResponse:
        job = _get_job(job_id)
        if not reason.strip():
            return _row(
                request,
                job,
                highlighted=False,
                error="Informe o motivo da correção.",
                status_code=400,
            )
        store.override_verdict(job_id, verdict, reason, now())
        return _row(request, store.job(job_id).job, highlighted=False)

    @app.post("/jobs/{job_id}/resume", response_class=HTMLResponse)
    def resume(request: Request, job_id: int) -> HTMLResponse:
        job = _get_job(job_id)
        try:
            tailor(job_id)
        except InventedFactsError:
            return _row(
                request,
                job,
                highlighted=False,
                error="Currículo recusado: contém informações que não constam no currículo base.",
            )
        except TailorRefused:
            return _row(
                request,
                job,
                highlighted=False,
                error="O modelo recusou gerar o currículo para esta vaga.",
            )
        except SpendLimitReached:
            return _row(
                request,
                job,
                highlighted=False,
                error="Limite de gastos da Anthropic atingido; tente novamente mais tarde.",
            )
        except MissingApiKey as exc:
            return _row(request, job, highlighted=False, error=str(exc))
        except Exception:  # never a bare 500: htmx would leave the row as it was
            logger.exception("Tailored resume for Job %d failed", job_id)
            return _row(
                request,
                job,
                highlighted=False,
                error="Não foi possível gerar o currículo (erro inesperado).",
            )
        return _row(request, store.job(job_id).job, highlighted=False)

    @app.get("/resumes/{filename}")
    def resume_pdf(filename: str) -> FileResponse:
        if Path(filename).name != filename or filename.startswith("."):
            raise HTTPException(404, "arquivo não encontrado")
        resumes_dir = paths.resumes_dir.resolve()
        path = (paths.resumes_dir / filename).resolve()
        if path.parent != resumes_dir or not path.is_file():
            raise HTTPException(404, "arquivo não encontrado")
        return FileResponse(path, media_type="application/pdf")

    return app
