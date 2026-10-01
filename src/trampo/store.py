"""SQLite-backed Store: all SQL for Boards, Postings, Jobs, Runs and batches lives here.

Schema in schema.sql, migrated via PRAGMA user_version. Timestamps are tz-aware
UTC datetimes in Python, ISO-8601 text in SQLite (see _to_iso/_from_iso).
"""

import json
import sqlite3
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path

from trampo.config import TrackId
from trampo.models import (
    Ats,
    BoardRef,
    JobRow,
    JobWithPostings,
    Judgment,
    Posting,
    Salary,
    StatusValue,
    VerdictValue,
)

# The code-made Verdict reason for a Job closed before it was judged (pt-BR, shown on the page).
CLOSED_REASON = "vaga encerrada"


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError(f"timestamp must be tz-aware (UTC): {value!r}")
    # Normalize to UTC so stored TEXT timestamps always end in "+00:00": a non-UTC but
    # aware datetime would otherwise keep its own offset, and TEXT ordering across mixed
    # offsets doesn't match chronological order.
    return value.astimezone(UTC).isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _posting_from_row(row: sqlite3.Row, company: str) -> Posting:
    salary = Salary.model_validate_json(row["salary"]) if row["salary"] is not None else None
    return Posting(
        ats=row["ats"],
        board_slug=row["board_slug"],
        posting_id=row["posting_id"],
        company=company,
        title=row["title"],
        location=row["location"],
        url=row["url"],
        description=row["description"],
        workplace=row["workplace"],
        salary=salary,
        published_at=_from_iso(row["published_at"]) if row["published_at"] is not None else None,
    )


class JobNotFound(LookupError):
    def __init__(self, job_id: int) -> None:
        super().__init__(f"Job {job_id} not found")


class Store:
    def __init__(self, path: Path) -> None:
        # check_same_thread=False: the web page (Task 17) shares one Store across
        # FastAPI's worker threads; sqlite3.threadsafety == 3 here, so a single
        # connection used from multiple threads (never concurrently) is safe.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        row = self._conn.execute("PRAGMA user_version").fetchone()
        assert row is not None
        if row[0] == 0:
            schema = resources.files("trampo").joinpath("schema.sql").read_text()
            self._conn.executescript(schema)

    def close(self) -> None:
        self._conn.close()

    # -- Boards ------------------------------------------------------------

    def add_board(self, board: BoardRef, company: str, now: datetime) -> bool:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO boards (ats, slug, company, active, added_at) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (board.ats, board.slug, company, _to_iso(now)),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def active_boards(self) -> list[tuple[BoardRef, str]]:
        rows = self._conn.execute("SELECT ats, slug, company FROM boards WHERE active = 1")
        return [(BoardRef(ats=r["ats"], slug=r["slug"]), r["company"]) for r in rows]

    def deactivate_board(self, board: BoardRef) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE boards SET active = 0 WHERE ats = ? AND slug = ?",
                (board.ats, board.slug),
            )

    def activate_board(self, board: BoardRef) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE boards SET active = 1 WHERE ats = ? AND slug = ?",
                (board.ats, board.slug),
            )

    # -- Postings and Jobs ---------------------------------------------------

    def posting_job_id(self, ats: Ats, posting_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT job_id FROM postings WHERE ats = ? AND posting_id = ?", (ats, posting_id)
        ).fetchone()
        return row["job_id"] if row is not None else None

    def find_recent_job(self, company: str, normalized_title: str, since: datetime) -> int | None:
        row = self._conn.execute(
            """
            SELECT j.id AS id, COALESCE(MAX(p.last_seen_at), j.created_at) AS last_activity
            FROM jobs j
            LEFT JOIN postings p ON p.job_id = j.id
            WHERE j.company = ? AND j.normalized_title = ?
            GROUP BY j.id
            HAVING last_activity >= ?
            ORDER BY last_activity DESC
            LIMIT 1
            """,
            (company, normalized_title, _to_iso(since)),
        ).fetchone()
        return row["id"] if row is not None else None

    def create_job(
        self, company: str, title: str, normalized_title: str, run_id: int, now: datetime
    ) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO jobs (company, title, normalized_title, created_run_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (company, title, normalized_title, run_id, _to_iso(now)),
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def upsert_posting(self, posting: Posting, job_id: int, now: datetime) -> None:
        now_iso = _to_iso(now)
        salary_json = posting.salary.model_dump_json() if posting.salary is not None else None
        published_at = _to_iso(posting.published_at) if posting.published_at is not None else None
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO postings
                    (ats, posting_id, board_slug, job_id, title, location, url, description,
                     workplace, salary, published_at, first_seen_at, last_seen_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT (ats, posting_id) DO UPDATE SET
                    board_slug = excluded.board_slug,
                    title = excluded.title,
                    location = excluded.location,
                    url = excluded.url,
                    description = excluded.description,
                    workplace = excluded.workplace,
                    salary = excluded.salary,
                    published_at = excluded.published_at,
                    last_seen_at = excluded.last_seen_at,
                    closed_at = NULL
                """,
                (
                    posting.ats,
                    posting.posting_id,
                    posting.board_slug,
                    job_id,
                    posting.title,
                    posting.location,
                    posting.url,
                    posting.description,
                    posting.workplace,
                    salary_json,
                    published_at,
                    now_iso,
                    now_iso,
                ),
            )
            row = self._conn.execute(
                "SELECT job_id FROM postings WHERE ats = ? AND posting_id = ?",
                (posting.ats, posting.posting_id),
            ).fetchone()
            assert row is not None
            self._conn.execute("UPDATE jobs SET closed_at = NULL WHERE id = ?", (row["job_id"],))
            # A reopened Job that code rejected as closed gets judged; a Verdict Claude
            # (judged_model_id) or the Candidate (an Override) set is never touched.
            self._conn.execute(
                """
                UPDATE jobs SET verdict = 'pending', verdict_reason = NULL
                WHERE id = ? AND verdict = 'rejected' AND verdict_reason = ?
                  AND judged_model_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM overrides o WHERE o.job_id = jobs.id)
                """,
                (row["job_id"], CLOSED_REASON),
            )

    def close_missing_postings(self, board: BoardRef, seen_ids: set[str], now: datetime) -> None:
        with self._conn:
            rows = self._conn.execute(
                "SELECT posting_id FROM postings "
                "WHERE ats = ? AND board_slug = ? AND closed_at IS NULL",
                (board.ats, board.slug),
            ).fetchall()
            missing = [r["posting_id"] for r in rows if r["posting_id"] not in seen_ids]
            self._conn.executemany(
                "UPDATE postings SET closed_at = ? WHERE ats = ? AND posting_id = ?",
                [(_to_iso(now), board.ats, posting_id) for posting_id in missing],
            )

    def close_jobs_without_open_postings(self, now: datetime) -> list[int]:
        with self._conn:
            rows = self._conn.execute(
                """
                SELECT j.id AS id FROM jobs j
                WHERE j.closed_at IS NULL
                  AND EXISTS (SELECT 1 FROM postings p WHERE p.job_id = j.id)
                  AND NOT EXISTS (
                      SELECT 1 FROM postings p WHERE p.job_id = j.id AND p.closed_at IS NULL
                  )
                """
            ).fetchall()
            ids = [r["id"] for r in rows]
            self._conn.executemany(
                "UPDATE jobs SET closed_at = ? WHERE id = ?",
                [(_to_iso(now), job_id) for job_id in ids],
            )
        return ids

    def reject_closed_pending(self, reason: str) -> int:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET verdict = 'rejected', verdict_reason = ? "
                "WHERE closed_at IS NOT NULL AND verdict = 'pending'",
                (reason,),
            )
        return cur.rowcount

    def dismiss_discarded(self, max_fit_score: int) -> int:
        """Dismiss every untouched (`new`) Job that is rejected or at/below
        max_fit_score. Overridden Jobs are the Candidate's call and never
        swept. Idempotent, so it also sweeps Jobs judged before this rule."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'dismissed' "
                "WHERE status = 'new' AND (verdict = 'rejected' OR fit_score <= ?) "
                "AND id NOT IN (SELECT job_id FROM overrides)",
                (max_fit_score,),
            )
        return cur.rowcount

    def pending_open_count(self) -> int:
        """Jobs still waiting for a Verdict that are open on their ATS."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE verdict = 'pending' AND closed_at IS NULL"
        ).fetchone()
        assert row is not None
        return row["n"]

    def jobs_to_judge(self) -> list[JobWithPostings]:
        open_job_ids: set[int] = set()
        for _, job_ids, _, _ in self.open_batches("judge"):
            open_job_ids.update(job_ids)
        # A Job without Postings can't be judged (judge.format_job reads its latest one).
        rows = self._conn.execute(
            "SELECT id FROM jobs WHERE verdict = 'pending' AND closed_at IS NULL "
            "AND EXISTS (SELECT 1 FROM postings p WHERE p.job_id = jobs.id) ORDER BY id"
        ).fetchall()
        return [self.job(r["id"]) for r in rows if r["id"] not in open_job_ids]

    def save_judgment(
        self, job_id: int, judgment: Judgment, model_id: str, prompt_hash: str
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE jobs SET
                    verdict = ?, verdict_reason = ?, track = ?,
                    fit_score = ?, fit_reason = ?, job_language = ?,
                    judged_model_id = ?, judged_prompt_hash = ?
                WHERE id = ?
                """,
                (
                    judgment.verdict,
                    judgment.reason,
                    judgment.track,
                    judgment.fit_score,
                    judgment.fit_reason,
                    judgment.job_language,
                    model_id,
                    prompt_hash,
                    job_id,
                ),
            )

    def set_verdict(
        self,
        job_id: int,
        verdict: VerdictValue,
        reason: str,
        model_id: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        """Set a Verdict. A Verdict Claude caused (a refusal) passes its model
        and prompt hash; a code-made one leaves the stored ones untouched."""
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET verdict = ?, verdict_reason = ?, "
                "judged_model_id = COALESCE(?, judged_model_id), "
                "judged_prompt_hash = COALESCE(?, judged_prompt_hash) "
                "WHERE id = ?",
                (verdict, reason, model_id, prompt_hash, job_id),
            )

    def set_status(self, job_id: int, status: StatusValue) -> None:
        with self._conn:
            self._conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))

    def set_notes(self, job_id: int, notes: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, job_id))

    def override_verdict(
        self, job_id: int, to_verdict: VerdictValue, reason: str, now: datetime
    ) -> None:
        with self._conn:
            row = self._conn.execute("SELECT verdict FROM jobs WHERE id = ?", (job_id,)).fetchone()
            assert row is not None
            self._conn.execute(
                "INSERT INTO overrides (job_id, from_verdict, to_verdict, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, row["verdict"], to_verdict, reason, _to_iso(now)),
            )
            self._conn.execute(
                "UPDATE jobs SET verdict = ?, verdict_reason = ? WHERE id = ?",
                (to_verdict, reason, job_id),
            )

    def overridden_jobs(self) -> list[tuple[JobWithPostings, VerdictValue]]:
        rows = self._conn.execute(
            """
            SELECT o.job_id AS job_id, o.to_verdict AS to_verdict
            FROM overrides o
            WHERE o.id = (SELECT MAX(id) FROM overrides o2 WHERE o2.job_id = o.job_id)
            """
        ).fetchall()
        return [(self.job(r["job_id"]), r["to_verdict"]) for r in rows]

    def job(self, job_id: int) -> JobWithPostings:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        posting_rows = self._conn.execute(
            "SELECT * FROM postings WHERE job_id = ? ORDER BY first_seen_at", (job_id,)
        ).fetchall()
        postings = [_posting_from_row(r, company=row["company"]) for r in posting_rows]
        locations: list[str] = []
        for posting in postings:
            if posting.location is not None and posting.location not in locations:
                locations.append(posting.location)
        # Most recently seen Posting first, for workplace/salary (Task 17, ruling R21).
        by_recency = sorted(
            zip(posting_rows, postings, strict=True),
            key=lambda pair: pair[0]["last_seen_at"],
            reverse=True,
        )
        workplace = by_recency[0][1].workplace if by_recency else None
        salary = next(
            (posting.salary for _, posting in by_recency if posting.salary is not None), None
        )
        # An undated Job shows the day a Run first saw it (docs/product.md, Eligibility).
        dates = [posting.published_at for posting in postings if posting.published_at is not None]
        published_at = min(
            dates or [_from_iso(r["first_seen_at"]) for r in posting_rows], default=None
        )
        job_row = JobRow(
            id=row["id"],
            company=row["company"],
            title=row["title"],
            normalized_title=row["normalized_title"],
            created_run_id=row["created_run_id"],
            created_at=_from_iso(row["created_at"]),
            closed_at=_from_iso(row["closed_at"]) if row["closed_at"] is not None else None,
            verdict=row["verdict"],
            verdict_reason=row["verdict_reason"],
            track=row["track"],
            fit_score=row["fit_score"],
            fit_reason=row["fit_reason"],
            job_language=row["job_language"],
            status=row["status"],
            notes=row["notes"],
            resume_path=row["resume_path"],
            workplace=workplace,
            published_at=published_at,
            salary=salary,
            locations=locations,
            urls=[posting.url for posting in postings],
        )
        return JobWithPostings(job=job_row, postings=postings)

    def list_jobs(
        self,
        *,
        status: StatusValue | None = None,
        track: TrackId | None = None,
        company: str | None = None,
        since: datetime | None = None,
    ) -> list[JobRow]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if track is not None:
            clauses.append("track = ?")
            params.append(track)
        if company is not None:
            clauses.append("company = ?")
            params.append(company)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(_to_iso(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT id FROM jobs {where} ORDER BY created_at DESC", params
        ).fetchall()
        return [self.job(r["id"]).job for r in rows]

    def save_resume(self, job_id: int, path: Path, model_id: str, prompt_hash: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET resume_path = ?, resume_model_id = ?, resume_prompt_hash = ? "
                "WHERE id = ?",
                (str(path), model_id, prompt_hash, job_id),
            )

    # -- Runs and batches ----------------------------------------------------

    def start_run(self, now: datetime) -> int:
        with self._conn:
            cur = self._conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_to_iso(now),))
        assert cur.lastrowid is not None
        return cur.lastrowid

    def finish_run(
        self,
        run_id: int,
        now: datetime,
        outcome: str,
        counts: dict[str, int],
        error: str | None,
        model_id: str | None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, outcome = ?, counts = ?, error = ?, "
                "model_id = ? WHERE id = ?",
                (_to_iso(now), outcome, json.dumps(counts), error, model_id, run_id),
            )

    def add_batch(
        self,
        batch_id: str,
        kind: str,
        run_id: int,
        job_ids: list[int],
        model_id: str,
        prompt_hash: str,
        now: datetime,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO batches "
                "(id, kind, run_id, job_ids, model_id, prompt_hash, submitted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (batch_id, kind, run_id, json.dumps(job_ids), model_id, prompt_hash, _to_iso(now)),
            )

    def open_batches(self, kind: str) -> list[tuple[str, list[int], str, str]]:
        """Uncollected batches as (batch id, job ids, model id, prompt hash), oldest first."""
        rows = self._conn.execute(
            "SELECT id, job_ids, model_id, prompt_hash FROM batches "
            "WHERE kind = ? AND collected_at IS NULL ORDER BY submitted_at",
            (kind,),
        ).fetchall()
        return [(r["id"], json.loads(r["job_ids"]), r["model_id"], r["prompt_hash"]) for r in rows]

    def mark_batch_collected(self, batch_id: str, now: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE batches SET collected_at = ? WHERE id = ?", (_to_iso(now), batch_id)
            )

    # -- Discovery, meta, backup ----------------------------------------------

    def discovery_searches_on(self, day: date) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM discovery_searches WHERE searched_on = ?",
            (day.isoformat(),),
        ).fetchone()
        assert row is not None
        return row["n"]

    def discovery_queries_since(self, day: date) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT query FROM discovery_searches WHERE searched_on >= ?",
            (day.isoformat(),),
        ).fetchall()
        return {r["query"] for r in rows}

    def log_discovery_search(self, day: date, query: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO discovery_searches (searched_on, query) VALUES (?, ?)",
                (day.isoformat(), query),
            )

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def backup_to(self, dest: Path) -> None:
        dest_conn = sqlite3.connect(dest)
        try:
            self._conn.backup(dest_conn)
            # The copy inherits WAL mode, which needs -wal/-shm files and shared
            # memory: unreliable on a cloud-synced /mnt/c folder.
            dest_conn.execute("PRAGMA journal_mode = DELETE")
        finally:
            dest_conn.close()
