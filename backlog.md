# Trampo — Implementation Backlog

> **For agentic workers:** implement the tasks in order, one at a time, test-first (superpowers:test-driven-development). Recommended executor: superpowers:subagent-driven-development (one implementer + one reviewer per task). Tick the checkboxes as you go; one commit per task.

**Goal:** build phase 1 of Trampo — a daily Run that discovers ATS Boards, collects Postings, groups them into Jobs, has Claude judge each Job, sends a Telegram Digest, renders Tailored resumes as PDF, and shows everything on a local htmx page.

**Architecture:** a deterministic pipeline in Python ([ADR 0001](docs/adr/0001-deterministic-pipeline-not-agent-loop.md)); Claude is called only for Discovery, judging and Tailored resumes, through the Batch API when nobody is waiting. SQLite with hand-written SQL ([ADR 0003](docs/adr/0003-plain-sqlite-no-orm.md)). Runs locally on WSL2, scheduled by Windows Task Scheduler.

**Tech stack:** Python 3.14, uv, anthropic, httpx2, pydantic, FastAPI + Jinja2 + uvicorn, htmx + Pico.css, Playwright, sqlite3, pytest, ruff, pyright.

**Spec:** [docs/product.md](docs/product.md), [docs/architecture.md](docs/architecture.md), [CONTEXT.md](CONTEXT.md), [docs/adr/](docs/adr/). The spec wins over this backlog; if they disagree, stop and ask the owner.

## Resuming in a fresh session

1. Read `CLAUDE.md`, then the spec files above.
2. Find the first task with unchecked boxes and continue from there.
3. Tasks that touch the Claude API (10, 11, 12, 16, 19): load the `claude-api` skill first and take every SDK call, parameter and error class from its Python docs. Names in this backlog are contracts **between our modules**, not SDK names.
4. 👤 marks steps that need the owner (a review, an account, a secret). Stop and ask; never guess personal data.

## Global constraints

- Python `>=3.14`, src layout, package `trampo`, entry point `trampo = "trampo.cli:main"`.
- Runtime dependencies, and nothing else: `anthropic`, `httpx2`, `pydantic`, `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `playwright`. Dev: `pytest`, `ruff`, `pyright`. Any other package needs the owner's approval.
- Identifiers use the CONTEXT.md terms: Board, Posting, Job, Verdict, Status, Override, Track, Fit score, Run, Digest, Base resume, Tailored resume.
- Verdict values: `pending` | `eligible` | `needs_review` | `rejected`. Status values: `new` | `seen` | `applied` | `dismissed`. Track ids: `backend` | `agents`. ATS ids: `greenhouse` | `lever` | `ashby`.
- English for code, comments, commits and docs. Portuguese (pt-BR) for web UI text, Telegram text, and the reasons Claude writes (Verdict reason, Fit score reason).
- Private data directory: env var `TRAMPO_PRIVATE_DIR`, default `private` (relative to the working directory). Never commit, print or paste its contents; examples use `private.example/`.
- Tests never touch the network: every HTTP call goes through `httpx2.MockTransport` serving files from `tests/fixtures/`. Playwright tests only render local HTML.
- Claude: resolve the newest Opus via the Models API at the start of each Run. Use only features stable across Opus versions: adaptive thinking, structured output via `output_config.format`, no assistant prefill, no forced `tool_choice`. Prompts live in `src/trampo/prompts/*.md`. Store `model_id` + `prompt_hash` (first 12 hex chars of the prompt file's SHA-256) on every Verdict and Tailored resume.
- Batch API for judging and automatic Tailored resumes; a regular call for on-demand Tailored resumes and Discovery.
- Values from the spec, all in `config.toml`: Job window 7 days, Repost window 30 days, automatic Tailored resume at Fit score ≥ 8, at most 10 Discovery searches per day.
- Timestamps: timezone-aware UTC in code, ISO-8601 text in SQLite; convert to America/Sao_Paulo only for display.
- The web server binds `127.0.0.1` only.
- Every task ends green: `uv run pytest`, `uv run pyright`, `uv run ruff check .`, `uv run ruff format --check .`; then one commit (`feat(<area>): …`, `test: …`, `chore: …`).

## Review Focus

The failure modes a real Run will hit that the happy path doesn't. Each has a test in its owning task.

1. **An ATS is down for one Board.** A failed fetch (timeout, 5xx) must not close that Board's Postings or Jobs; only a successful fetch that no longer lists a Posting closes it. → Task 14
2. **The Anthropic spend limit is hit mid-Run.** The Run finishes without crashing, affected Jobs stay `pending`, exactly one Telegram alert goes out; the next Run judges `pending` Jobs that are still open and rejects closed ones with the reason "vaga encerrada". → Tasks 10, 14
3. **The process dies while a batch is in flight** (PC turned off). The next Run collects that batch before submitting anything, and no Job is judged twice. → Task 14
4. **A Tailored resume adds a fact.** A company, date or number that isn't in the Base resume makes the Tailored resume be refused: not saved, logged, reported. → Task 16
5. **Keyword matching is too loose.** "Backend Engineer, Internal Tools" must not be dropped by `intern`; "Django Engineer" must not match `go engineer`; "go-to-market" in a description must not count as Go. → Task 8

## File map

```
pyproject.toml  uv.lock  .python-version  config.toml
.github/workflows/ci.yml
src/trampo/
  __init__.py
  cli.py              argparse entry point; each task registers its own subcommand
  config.py           Config, Track, Profile, Paths + loaders
  models.py           domain types shared by every module
  schema.sql          full schema; migrations via PRAGMA user_version
  store.py            every SQL statement lives here
  ats/__init__.py     AtsClient Protocol, BoardNotFound, client_for(), html_to_text()
  ats/greenhouse.py   ats/lever.py   ats/ashby.py
  prefilter.py        objective rules before any Claude call
  dedup.py            normalize_title(), assign_job()
  claude.py           client factory, newest_opus(), load_prompt(), batch helpers, SpendLimitReached
  judge.py            judge request builder + result parsing
  discovery.py        Board discovery through Claude's web search tool
  digest.py           Telegram client + pt-BR message formatting
  pipeline.py         run(): one Run end to end
  resume/model.py     Resume (JSON Resume subset) + load_resume()
  resume/render.py    Jinja2 HTML + Playwright PDF
  resume/tailor.py    Tailored resume request, invented_facts() guard
  resume/templates/resume.html
  web/__init__.py     create_app()
  web/templates/*.html   web/static/htmx.min.js   web/static/pico.min.css
  backup.py           copy private/ to the cloud-synced folder
  evaluate.py         trampo eval
  prompts/judge.md    prompts/tailor.md
tests/                mirrors src/; recorded responses in tests/fixtures/{greenhouse,lever,ashby,anthropic,telegram}/
private.example/      resume.json, profile.toml, .env.example (fictional Candidate "Alex Silva")
```

---

## Phase 1

### Task 1: Project scaffold and CI

**Files:** create `pyproject.toml`, `.python-version`, `src/trampo/__init__.py`, `src/trampo/cli.py`, `tests/test_cli.py`, `.github/workflows/ci.yml`

**Produces:** `trampo.cli.build_parser() -> argparse.ArgumentParser` (with a `subparsers` object later tasks add to) and `trampo.cli.main(argv: list[str] | None = None) -> int`.

`pyproject.toml` essentials:

```toml
[project]
name = "trampo"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []          # filled by `uv add` below

[project.scripts]
trampo = "trampo.cli:main"

[dependency-groups]
dev = []                   # filled by `uv add --dev` below

[build-system]
requires = ["uv_build"]
build-backend = "uv_build"

[tool.ruff]
line-length = 100
target-version = "py314"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pyright]
pythonVersion = "3.14"
include = ["src", "tests"]
typeCheckingMode = "standard"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [x] `echo 3.14 > .python-version`; write `pyproject.toml`; `uv add anthropic httpx pydantic fastapi uvicorn jinja2 python-multipart playwright`; `uv add --dev pytest ruff pyright`.
- [x] Test first — `tests/test_cli.py::test_version`: `main(["--version"])` raises `SystemExit(0)` and prints `trampo 0.1.0`.
- [x] Implement `build_parser()` (argparse, `prog="trampo"`, `--version`, `add_subparsers(dest="command")`) and `main()`.
- [x] `.github/workflows/ci.yml`: on push and pull_request, ubuntu-latest, `actions/checkout` + `astral-sh/setup-uv` (current major versions — check their READMEs), then `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, `uv run playwright install --with-deps chromium`, `uv run pytest`.
- [x] Confirm the `.claude/settings.json` ruff hook now formats edited `.py` files.
- [x] Green checks, commit `chore: scaffold project and CI`.

### Task 2: Config, profile and private paths

**Files:** create `config.toml`, `src/trampo/config.py`, `private.example/profile.toml`, `tests/test_config.py`

**Produces:**

```python
TrackId = Literal["backend", "agents"]

class Track(BaseModel):
    id: TrackId
    name: str
    title_keywords: list[str]
    title_keywords_requiring_go: list[str] = []   # match only if the description mentions Go
    emphasis: str                                  # what the Tailored resume highlights

class Config(BaseModel):
    max_age_days: int
    repost_days: int
    discovery_max_searches_per_day: int
    auto_resume_min_fit_score: int
    ignore_title_keywords: list[str]
    tracks: list[Track]

class SalaryFloor(BaseModel):
    clt_brl: int
    pj_brl: int
    international_usd: int

class Profile(BaseModel):
    accepted_contracts: list[Literal["clt", "pj", "international_contractor"]]
    salary_floor: SalaryFloor
    backup_dir: Path | None          # empty string in TOML -> None

@dataclass(frozen=True)
class Paths:
    private_dir: Path
    db: Path              # <private>/trampo.db
    resume_json: Path     # <private>/resume.json
    profile_toml: Path    # <private>/profile.toml
    resumes_dir: Path     # <private>/resumes
    logs_dir: Path        # <private>/logs

def private_paths(env: Mapping[str, str] = os.environ) -> Paths
def load_config(path: Path = Path("config.toml")) -> Config
def load_profile(path: Path) -> Profile
```

`config.toml` (public; values from docs/product.md):

```toml
max_age_days = 7
repost_days = 30
discovery_max_searches_per_day = 10
auto_resume_min_fit_score = 8
ignore_title_keywords = ["frontend", "mobile", "ios", "android", "data scientist", "manager", "intern", "junior"]

[[tracks]]
id = "backend"
name = "Backend"
title_keywords = ["backend", "back-end", "golang", "go engineer"]
title_keywords_requiring_go = ["software engineer"]
emphasis = "Go, distributed systems, Kafka, PostgreSQL, fintech"

[[tracks]]
id = "agents"
name = "Agents/Automation"
title_keywords = ["agent engineer", "ai agent engineer", "agent ai engineer", "automation engineer"]
emphasis = "day-to-day AI tooling, this project, backend as the foundation"
```

`private.example/profile.toml` mirrors the owner's `private/profile.toml` shape (`accepted_contracts`, `[salary_floor]`, `[backup] dir`) with sample values.

- [x] Tests first, `tests/test_config.py`:
  - `test_loads_repo_config` — two Tracks, `max_age_days == 7`, `auto_resume_min_fit_score == 8`.
  - `test_missing_field_names_it` — a TOML without `repost_days` raises `ValidationError` mentioning `repost_days`.
  - `test_loads_example_profile` — `private.example/profile.toml` loads; its `[backup] dir = ""` becomes `backup_dir is None`.
  - `test_private_dir_from_env` — `private_paths({"TRAMPO_PRIVATE_DIR": "/x"}).db == Path("/x/trampo.db")`; the default is `private/`.
- [x] Implement `config.py` with `tomllib` and Pydantic (the profile's `[backup] dir` maps to `backup_dir`).
- [x] Green checks, commit `feat(config): load config, profile and private paths`.

### Task 3: Base resume model, example Candidate and the owner's resume 👤

**Files:** create `src/trampo/resume/__init__.py`, `src/trampo/resume/model.py`, `private.example/resume.json`, `tests/resume/test_model.py`; owner-only: `private/resume.json`

**Produces:** a JSON Resume subset (field names exactly as in https://jsonresume.org/schema):

```python
class Location(BaseModel): city: str | None = None; countryCode: str | None = None
class SocialProfile(BaseModel): network: str; url: str  # JSON Resume "profiles" (LinkedIn, GitHub); not config.Profile
class Basics(BaseModel): name: str; label: str; email: str; phone: str | None = None
                         summary: str; location: Location | None = None; profiles: list[SocialProfile] = []
class Work(BaseModel): name: str; position: str; startDate: str; endDate: str | None = None
                       summary: str | None = None; highlights: list[str] = []
class Education(BaseModel): institution: str; area: str; studyType: str
                            startDate: str | None = None; endDate: str | None = None
class Skill(BaseModel): name: str; keywords: list[str] = []
class Language(BaseModel): language: str; fluency: str
class Project(BaseModel): name: str; description: str; highlights: list[str] = []; url: str | None = None
class Resume(BaseModel): basics: Basics; work: list[Work]; education: list[Education] = []
                         skills: list[Skill] = []; languages: list[Language] = []; projects: list[Project] = []

def load_resume(path: Path) -> Resume
```

Dates are `YYYY-MM` or `YYYY-MM-DD` (validator).

- [x] Tests first: `test_example_resume_loads`; `test_missing_name_fails`; `test_bad_date_fails` (`"12/2024"` → `ValidationError`).
- [x] Implement the model; write `private.example/resume.json` for the fictional "Alex Silva, Backend Engineer" (fake email, phone and companies).
- [x] 👤 Convert the owner's CV: extract text with `uvx --from pypdf python -c "…"` from `private/CV_Bruno_Diego_Lima_de_Oliveira.pdf` (the text comes one word per line — rejoin it), write `private/resume.json`, check that `load_resume` accepts it, then show the owner a readable summary and apply their corrections. Ask whether to add this project as a `projects` entry. **Do not continue until the owner approves.** Never commit this file.
- [x] Green checks, commit `feat(resume): Base resume model and example Candidate`.

### Task 4: Domain models and Store

**Files:** create `src/trampo/models.py`, `src/trampo/schema.sql`, `src/trampo/store.py`, `tests/test_store.py`

**Produces** (`models.py`):

```python
Ats = Literal["greenhouse", "lever", "ashby"]
VerdictValue = Literal["pending", "eligible", "needs_review", "rejected"]
StatusValue = Literal["new", "seen", "applied", "dismissed"]
Workplace = Literal["remote", "hybrid", "onsite"]

class BoardRef(BaseModel, frozen=True): ats: Ats; slug: str

class Salary(BaseModel): min: float | None; max: float | None; currency: str; interval: str  # "year" | "month" | "hour"

class Posting(BaseModel):
    ats: Ats; board_slug: str; posting_id: str; company: str; title: str
    location: str | None; url: str; description: str        # plain text
    workplace: Workplace | None; salary: Salary | None
    published_at: datetime | None                            # None when the ATS gives no reliable date

class Judgment(BaseModel):                                   # Claude's structured output (Task 11)
    track: TrackId
    verdict: Literal["eligible", "needs_review", "rejected"]
    reason: str                                              # pt-BR
    remote: bool
    open_to_brazil: bool | None
    requires_us_work_authorization: bool | None
    fit_score: int = Field(ge=0, le=10)
    fit_reason: str                                          # pt-BR
    job_language: Literal["en", "pt"]

class JobRow(BaseModel):
    id: int; company: str; title: str; normalized_title: str
    created_run_id: int; created_at: datetime; closed_at: datetime | None
    verdict: VerdictValue; verdict_reason: str | None; track: TrackId | None
    fit_score: int | None; fit_reason: str | None; job_language: str | None
    status: StatusValue; notes: str
    resume_path: str | None
    locations: list[str]; urls: list[str]                   # from its Postings

@dataclass(frozen=True)
class JobWithPostings: job: JobRow; postings: list[Posting]
```

`schema.sql` (version 1; set `PRAGMA user_version = 1`):

```sql
CREATE TABLE boards (
  ats TEXT NOT NULL, slug TEXT NOT NULL, company TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL,
  PRIMARY KEY (ats, slug));
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL, normalized_title TEXT NOT NULL,
  created_run_id INTEGER NOT NULL, created_at TEXT NOT NULL, closed_at TEXT,
  verdict TEXT NOT NULL DEFAULT 'pending', verdict_reason TEXT, track TEXT,
  fit_score INTEGER, fit_reason TEXT, job_language TEXT,
  judged_model_id TEXT, judged_prompt_hash TEXT,
  status TEXT NOT NULL DEFAULT 'new', notes TEXT NOT NULL DEFAULT '',
  resume_path TEXT, resume_model_id TEXT, resume_prompt_hash TEXT);
CREATE INDEX jobs_by_title ON jobs (company, normalized_title);
CREATE TABLE postings (
  ats TEXT NOT NULL, posting_id TEXT NOT NULL, board_slug TEXT NOT NULL,
  job_id INTEGER NOT NULL REFERENCES jobs (id),
  title TEXT NOT NULL, location TEXT, url TEXT NOT NULL, description TEXT NOT NULL,
  workplace TEXT, salary TEXT, published_at TEXT,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, closed_at TEXT,
  PRIMARY KEY (ats, posting_id));
CREATE TABLE overrides (
  id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs (id),
  from_verdict TEXT NOT NULL, to_verdict TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  model_id TEXT, outcome TEXT, counts TEXT, error TEXT);          -- outcome: ok | failed; counts: JSON
CREATE TABLE batches (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, run_id INTEGER NOT NULL,   -- kind: judge | resume
  job_ids TEXT NOT NULL, submitted_at TEXT NOT NULL, collected_at TEXT);
CREATE TABLE discovery_searches (id INTEGER PRIMARY KEY, searched_on TEXT NOT NULL, query TEXT NOT NULL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

`Store` API (add small helpers if needed; keep these names):

```python
class Store:
    def __init__(self, path: Path) -> None   # foreign_keys=ON, WAL, applies schema when user_version == 0
    # Boards
    def add_board(self, board: BoardRef, company: str, now: datetime) -> bool      # False if it already exists
    def active_boards(self) -> list[tuple[BoardRef, str]]                          # (board, company)
    def deactivate_board(self, board: BoardRef) -> None
    # Postings and Jobs
    def posting_job_id(self, ats: Ats, posting_id: str) -> int | None
    def find_recent_job(self, company: str, normalized_title: str, since: datetime) -> int | None
    def create_job(self, company: str, title: str, normalized_title: str, run_id: int, now: datetime) -> int
    def upsert_posting(self, posting: Posting, job_id: int, now: datetime) -> None  # keeps first_seen_at; clears closed_at; reopens the Job
    def close_missing_postings(self, board: BoardRef, seen_ids: set[str], now: datetime) -> None
    def close_jobs_without_open_postings(self, now: datetime) -> list[int]
    def reject_closed_pending(self, reason: str) -> int
    def jobs_to_judge(self) -> list[JobWithPostings]   # verdict pending, not closed, not in an uncollected judge batch
    def save_judgment(self, job_id: int, judgment: Judgment, model_id: str, prompt_hash: str) -> None
    def set_verdict(self, job_id: int, verdict: VerdictValue, reason: str) -> None
    def set_status(self, job_id: int, status: StatusValue) -> None
    def set_notes(self, job_id: int, notes: str) -> None
    def override_verdict(self, job_id: int, to_verdict: VerdictValue, reason: str, now: datetime) -> None
    def overridden_jobs(self) -> list[tuple[JobWithPostings, VerdictValue]]       # for trampo eval
    def job(self, job_id: int) -> JobWithPostings
    def list_jobs(self, *, status: StatusValue | None = None, track: TrackId | None = None,
                  company: str | None = None, since: datetime | None = None) -> list[JobRow]
    def save_resume(self, job_id: int, path: Path, model_id: str, prompt_hash: str) -> None
    # Runs and batches
    def start_run(self, now: datetime) -> int
    def finish_run(self, run_id: int, now: datetime, outcome: str, counts: dict[str, int],
                   error: str | None, model_id: str | None) -> None
    def add_batch(self, batch_id: str, kind: str, run_id: int, job_ids: list[int], now: datetime) -> None
    def open_batches(self, kind: str) -> list[tuple[str, list[int]]]
    def mark_batch_collected(self, batch_id: str, now: datetime) -> None
    # Discovery, meta, backup
    def discovery_searches_on(self, day: date) -> int
    def discovery_queries_since(self, day: date) -> set[str]
    def log_discovery_search(self, day: date, query: str) -> None
    def get_meta(self, key: str) -> str | None
    def set_meta(self, key: str, value: str) -> None
    def backup_to(self, dest: Path) -> None             # sqlite3 online backup API
```

- [x] Tests first, `tests/test_store.py` (a temporary DB file per test):
  - `test_schema_applied_once` — reopening the same file keeps data and `user_version == 1`.
  - `test_upsert_posting_keeps_first_seen` — a second upsert updates `last_seen_at` only.
  - `test_close_missing_postings_only_touches_that_board`.
  - `test_upsert_reopens_closed_job` — a closed Job whose Posting reappears has `closed_at` cleared.
  - `test_find_recent_job_respects_since`.
  - `test_jobs_to_judge_skips_jobs_in_open_batches`.
  - `test_override_records_from_and_to` — the `overrides` row holds the previous Verdict and the Job's Verdict changes.
  - `test_list_jobs_filters` — by status, track and company.
  - `test_reject_closed_pending` — only closed `pending` Jobs become `rejected` with the given reason.
  - `test_backup_to_while_open` — the copy opens and holds the same rows.
- [x] Implement `models.py`, `schema.sql`, `store.py` (load the schema with `importlib.resources`; JSON columns via `json`).
- [x] Green checks, commit `feat(store): domain models and SQLite store`.

### Task 5: ATS client protocol and Greenhouse

**Files:** create `src/trampo/ats/__init__.py`, `src/trampo/ats/greenhouse.py`, `tests/ats/test_greenhouse.py`, `tests/fixtures/greenhouse/jobs.json`

**Produces:**

```python
class BoardNotFound(Exception): ...

class AtsClient(Protocol):
    ats: Ats
    def fetch_postings(self, slug: str, company: str) -> list[Posting]: ...   # BoardNotFound on 404; other httpx2 errors propagate

def client_for(ats: Ats, http: httpx2.Client) -> AtsClient
def html_to_text(html: str) -> str          # stdlib html.parser; keeps paragraph breaks
```

Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` → `jobs[]`: `id` → `posting_id`, `title`, `absolute_url` → `url`, `location.name` → `location`, `content` (HTML-escaped: `html.unescape` then `html_to_text`) → `description`, `first_published` → `published_at` when present, else `None` (`updated_at` is **not** a publish date). No structured workplace or salary → `None`.

- [x] Record the fixture: fetch a real public board (e.g. `gitlab`) once with curl, keep 3 jobs, and include one without `first_published`.
- [x] Tests first:
  - `test_parses_postings` — count, ids as strings, URL, location.
  - `test_description_is_plain_text` — no HTML tags or entities left.
  - `test_published_at_is_utc_or_none`.
  - `test_404_raises_board_not_found`.
  - `test_client_for_returns_greenhouse`.
- [x] Implement; green checks; commit `feat(ats): client protocol and Greenhouse`.

### Task 6: Lever client

**Files:** create `src/trampo/ats/lever.py`, `tests/ats/test_lever.py`, `tests/fixtures/lever/postings.json`; modify `src/trampo/ats/__init__.py` (`client_for`)

Lever: `GET https://api.lever.co/v0/postings/{slug}?mode=json` → list: `id`, `text` → `title`, `hostedUrl` → `url`, `categories.location` → `location`, `workplaceType` (`remote` | `hybrid` | `on-site` | `unspecified`) → `remote` / `hybrid` / `onsite` / `None`, `createdAt` (epoch milliseconds) → `published_at`, `descriptionPlain` + each `lists[].text` + `html_to_text(lists[].content)` + `additionalPlain` → `description`, `salaryRange {min, max, currency, interval}` → `Salary`. EU-instance boards (`api.eu.lever.co`) are out of scope for now.

- [x] Record the fixture from a real public Lever board (3 postings, one with `salaryRange`, one `hybrid`).
- [x] Tests first: `test_parses_postings`, `test_workplace_mapping` (all four values), `test_created_at_millis_to_utc`, `test_salary_range`, `test_404_raises_board_not_found`.
- [x] Implement; green checks; commit `feat(ats): Lever client`.

### Task 7: Ashby client

**Files:** create `src/trampo/ats/ashby.py`, `tests/ats/test_ashby.py`, `tests/fixtures/ashby/job-board.json`; modify `src/trampo/ats/__init__.py`

Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` → `jobs[]`: skip `isListed == false`; `id`, `title`, `jobUrl` → `url`, `location` (+ `secondaryLocations` joined) → `location`, `workplaceType` (`Remote` | `Hybrid` | `OnSite`) or `isRemote` → `workplace`, `publishedAt` → `published_at`, `descriptionPlain` → `description`, the `Salary` component of `compensation.summaryComponents` → `Salary` (interval `"1 YEAR"` → `year`).

- [x] Record the fixture from a real public Ashby board (3 jobs, one unlisted, one with compensation).
- [x] Tests first: `test_parses_listed_postings_only`, `test_workplace_mapping`, `test_compensation_to_salary`, `test_404_raises_board_not_found`.
- [x] Implement; green checks; commit `feat(ats): Ashby client`.

### Task 8: Pre-filter

**Files:** create `src/trampo/prefilter.py`, `tests/test_prefilter.py`

**Produces:**

```python
@dataclass(frozen=True)
class Dropped:
    reason: str                     # English, for logs and Run counts

def mentions_go(description: str) -> bool
def title_track(title: str, description: str, config: Config) -> TrackId | None
def prefilter(posting: Posting, now: datetime, config: Config) -> TrackId | Dropped
```

Rules, in order, all case-insensitive and on **word boundaries**:

1. A title containing an `ignore_title_keywords` entry → `Dropped("ignored title keyword: <kw>")`.
2. No Track matches the title → `Dropped("no track keyword")`. `title_keywords_requiring_go` match only when `mentions_go(description)`.
3. `workplace` is `hybrid` or `onsite` → `Dropped("not remote")`.
4. `published_at` (or `now` when it is `None` — first seen now) older than `max_age_days` → `Dropped("older than N days")`.

`mentions_go`: true for `golang` (any case) or the standalone word `Go` capitalized and not part of a hyphenated word (`(?<![\w-])Go(?![\w-])`).

- [x] Tests first (Review Focus 5):
  - `test_internal_tools_is_not_intern` — "Backend Engineer, Internal Tools" → `backend`.
  - `test_django_is_not_go_engineer` — "Django Engineer" → `Dropped`.
  - `test_software_engineer_needs_go` — with "We use Go and Kafka" → `backend`; with "our go-to-market team" → `Dropped`; with "golang" → `backend`.
  - `test_agent_titles` — "AI Agent Engineer" → `agents`.
  - `test_automation_engineer_kept` — "Automation Engineer" → `agents` (the QA sense is left to Claude).
  - `test_ignored_keyword` — "Senior Frontend Engineer" → `Dropped`.
  - `test_hybrid_dropped`, `test_old_posting_dropped`, `test_undated_posting_kept`.
- [x] Implement; green checks; commit `feat(prefilter): objective rules before judging`.

### Task 9: Dedupe into Jobs

**Files:** create `src/trampo/dedup.py`, `tests/test_dedup.py`

**Produces:**

```python
def normalize_company(company: str) -> str
def normalize_title(title: str) -> str
def assign_job(store: Store, posting: Posting, run_id: int, now: datetime,
               repost_days: int) -> tuple[int, bool]      # (job_id, created)
```

`normalize_title`: lowercase; strip accents; remove `(...)` and `[...]` parts; remove a trailing ` - X`, ` – X`, ` | X` or `, X` **only when X is a location or remote word** (remote, brazil, brasil, latam, americas, worldwide, anywhere, global, argentina, mexico, colombia, chile, us, usa, canada, emea); collapse whitespace; strip punctuation at the ends.

`assign_job`: a known Posting (same ATS + ID) → its Job, `created=False`. Otherwise a Job with the same normalized company and title whose last activity is within `repost_days` → that Job (a Repost, or the same Job in another location). Otherwise → `store.create_job(...)`, `created=True`. The caller then calls `store.upsert_posting`.

- [x] Tests first:
  - `test_location_variants_normalize_equal` — "Senior Backend Engineer (Remote - LATAM)", "Senior Backend Engineer - Brazil" and "senior backend engineer" are equal.
  - `test_team_suffix_kept` — "Senior Engineer - Payments" ≠ "Senior Engineer - Platform".
  - `test_same_run_locations_share_job` — two Postings, same company and title, different locations → one Job.
  - `test_repost_within_window_keeps_job_and_status` — a Job marked `applied` 20 days ago + a new Posting ID → same Job, Status still `applied`.
  - `test_repost_after_window_creates_new_job` — the same after 40 days → a new Job.
- [x] Implement; green checks; commit `feat(dedup): group Postings into Jobs`.

### Task 10: Claude foundation

Load the `claude-api` skill first.

**Files:** create `src/trampo/claude.py`, `tests/test_claude.py`, `tests/fixtures/anthropic/*.json`

**Produces:**

```python
class SpendLimitReached(Exception): ...

@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    hash: str            # sha256(text)[:12]

def make_client(http: httpx2.Client | None = None) -> anthropic.Anthropic   # ANTHROPIC_API_KEY from env; tests pass a MockTransport client
def newest_opus(client: anthropic.Anthropic) -> str                        # Models API: ids starting "claude-opus-", newest created_at
def load_prompt(name: str) -> Prompt                                       # src/trampo/prompts/<name>.md via importlib.resources
def submit_batch(client: anthropic.Anthropic, requests: dict[str, dict]) -> str        # custom_id -> Messages params; returns the batch id
def batch_results(client: anthropic.Anthropic, batch_id: str) -> dict[str, Message | str] | None
    # None while still processing; str = that request's error; results keyed by custom_id, never by position
```

Every API call made through this module translates the "spend limit reached" API error into `SpendLimitReached`. Find out from the skill's error docs how the API reports a workspace spend limit, and record that response as a fixture.

- [x] Tests first:
  - `test_newest_opus_picks_latest_created` — a mocked models list with two Opus models and one Sonnet.
  - `test_prompt_hash_stable` — the same file gives the same 12-character hash.
  - `test_batch_results_none_while_processing`.
  - `test_batch_results_keyed_by_custom_id` — results in shuffled order.
  - `test_spend_limit_maps_to_exception` — Review Focus 2.
- [x] Implement; green checks; commit `feat(claude): client, model resolution, prompts and batches`.

### Task 11: Judge

Load the `claude-api` skill first.

**Files:** create `src/trampo/prompts/judge.md`, `src/trampo/judge.py`, `tests/test_judge.py`

**Produces:**

```python
def judge_params(item: JobWithPostings, resume: Resume, config: Config, profile: Profile,
                 prompt: Prompt, model: str) -> dict        # Messages API params for one Job
def parse_judgment(message: Message) -> Judgment | None     # None when stop_reason is "refusal"
```

- `judge.md` holds the instructions: the Eligibility section of docs/product.md, the two Tracks and their emphasis, the Salary floor rules, and "write `reason` and `fit_reason` in Portuguese (pt-BR)". Placeholders are filled from `Config` and `Profile`.
- Params: `model`; adaptive thinking; system = [rendered instructions, Base resume JSON] with `cache_control` on the last system block (the stable prefix); one user message with company, title, locations, workplace, salary and description; `output_config.format` = the JSON schema of `Judgment`.
- A refusal becomes `needs_review` with the reason "análise recusada pelo modelo" (the caller does this in Task 14).

- [x] Tests first:
  - `test_params_shape` — model, adaptive thinking, `cache_control` on the last system block, schema contains every `Judgment` field.
  - `test_prefix_identical_across_jobs` — two Jobs produce byte-identical system blocks (the cache hits).
  - `test_parse_valid_judgment`.
  - `test_parse_refusal_returns_none`.
  - `test_fit_score_out_of_range_rejected` — 11 → `ValidationError`.
- [x] Implement; green checks; commit `feat(judge): judge requests and parsing`.

### Task 12: Discovery

Load the `claude-api` skill first (server-side web search tool).

**Files:** create `src/trampo/discovery.py`, `tests/test_discovery.py`

**Produces:**

```python
def board_refs_from_urls(urls: Iterable[str]) -> set[BoardRef]
def discovery_queries(config: Config) -> list[str]
def discover(client: anthropic.Anthropic, store: Store, config: Config,
             ats_clients: dict[Ats, AtsClient], today: date, model: str) -> list[BoardRef]
```

- URL patterns: `jobs.ashbyhq.com/<slug>`, `job-boards.greenhouse.io/<slug>`, `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>` (skip `embed` and similar non-slug segments).
- `discovery_queries`: a deterministic list combining each ATS `site:` domain with each Track keyword and a remote term (`remote`, `LATAM`, `Brazil`).
- `discover`: runs `discovery_max_searches_per_day - store.discovery_searches_on(today)` searches, preferring queries not used in the last 14 days. Each search is one Messages call with the web search tool restricted by `allowed_domains` to the ATS domains and `max_uses=1`. URLs are read from the tool result blocks (the model's text is ignored). Every search is logged. Each new BoardRef is validated with `fetch_postings` (`BoardNotFound` → skip) and added with a company name derived from the slug ("acme-corp" → "Acme Corp").

- [x] Tests first:
  - `test_board_refs_from_urls` — all four patterns, junk URLs ignored, duplicates collapsed.
  - `test_respects_daily_quota` — 8 searches already logged today → exactly 2 calls.
  - `test_invalid_board_not_added`.
  - `test_known_board_not_duplicated`.
- [x] Implement; green checks; commit `feat(discovery): find Boards via web search`.

### Task 13: Digest and Telegram

**Files:** create `src/trampo/digest.py`, `tests/test_digest.py`, `tests/fixtures/telegram/sendMessage-ok.json`

**Produces:**

```python
TELEGRAM_LIMIT = 4096

def format_digest(jobs: list[JobRow], needs_review: int, rejected: int) -> list[str]   # pt-BR, HTML parse mode, each ≤ 4096 chars
def format_failure(error: str) -> str
def format_spend_cap_alert(pending: int) -> str

class Telegram:
    def __init__(self, http: httpx2.Client, token: str, chat_id: str) -> None
    def send(self, text: str) -> None      # sendMessage, parse_mode=HTML, link previews off; raises on a non-ok response
```

One line per Job: company, title, Track, Fit score, link to the Posting. A final line with the `needs_review` and `rejected` counts.

- [x] Tests first:
  - `test_escapes_html` — a title like "C++ & Go <Senior>" is escaped.
  - `test_splits_long_digest` — 80 Jobs → several messages, each ≤ 4096 characters, no Job split across two.
  - `test_counts_line`.
  - `test_send_posts_expected_payload` — MockTransport asserts the URL, `chat_id`, `parse_mode`.
- [x] Implement; green checks; commit `feat(digest): Telegram Digest`.

### Task 14: The Run pipeline (`trampo run`)

**Files:** create `src/trampo/pipeline.py`, `tests/test_pipeline.py`; modify `src/trampo/cli.py`

**Produces:**

```python
@dataclass
class RunContext:
    store: Store; config: Config; profile: Profile; paths: Paths
    claude: anthropic.Anthropic; ats_clients: dict[Ats, AtsClient]; telegram: Telegram
    now: Callable[[], datetime]; sleep: Callable[[float], None]
    poll_interval: float = 30.0

@dataclass
class RunSummary:
    run_id: int; new_jobs: int; eligible: int; needs_review: int; rejected: int; pending: int
    board_errors: int

def run(ctx: RunContext) -> RunSummary
```

A Run, in order:

1. `start_run`; `model = newest_opus(...)`.
2. **Recover:** collect every open `judge` batch (save judgments, mark collected); still processing → leave its Jobs alone this Run.
3. **Discovery** (`discover`).
4. **Collect** each active Board. `BoardNotFound` → `deactivate_board`. Any other error → log it, count `board_errors`, and **do not** close that Board's Postings. On success → `close_missing_postings`.
5. For each fetched Posting: known → `upsert_posting`; new → `prefilter`, and if kept → `assign_job` + `upsert_posting`.
6. `close_jobs_without_open_postings`; `reject_closed_pending("vaga encerrada")`.
7. **Judge:** `jobs_to_judge()` → one batch (`custom_id = "judge-<job_id>"`), `add_batch`, poll with `ctx.sleep(poll_interval)` until done. Refusal → `needs_review`; errored request → stays `pending`. `SpendLimitReached` → stop judging, send `format_spend_cap_alert` once, and continue the Run.
8. **Digest:** new `eligible` Jobs created in this Run → send only if there is at least one.
9. `finish_run(outcome="ok", counts=...)`.

Tailored resumes (Task 16) and backup (Task 18) plug in after step 8.

CLI: `trampo run` builds the context from the env (`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) and `private_paths()`, and logs to `<private>/logs/YYYY-MM-DD.log` and stderr. On an unexpected exception: `finish_run(outcome="failed", error=...)`, `Telegram.send(format_failure(...))`, exit code 1.

- [x] Tests first (a temporary private dir; MockTransport for ATS, Anthropic and Telegram; `sleep` is a no-op):
  - `test_happy_path_sends_digest` — fixture Boards → one eligible Job → one Digest containing it.
  - `test_second_run_is_quiet` — same data again → no new Jobs, no Digest.
  - `test_board_outage_does_not_close_postings` — Review Focus 1.
  - `test_spend_limit_keeps_pending_and_alerts_once` — Review Focus 2; then a second Run judges the still-open ones and rejects the closed one with "vaga encerrada".
  - `test_in_flight_batch_is_collected_not_resubmitted` — Review Focus 3.
  - `test_refusal_becomes_needs_review`.
  - `test_unexpected_error_marks_run_failed_and_alerts`.
- [x] Implement; register `run` in `cli.py`; green checks; commit `feat(pipeline): daily Run`.

### Task 15: Resume rendering (HTML → PDF)

**Files:** create `src/trampo/resume/render.py`, `src/trampo/resume/templates/resume.html`, `tests/resume/test_render.py`; modify `src/trampo/cli.py`

**Produces:**

```python
def render_html(resume: Resume, lang: Literal["en", "pt"]) -> str
def html_to_pdf(html: str, out: Path) -> None          # Playwright Chromium, A4
def resume_filename(company: str, title: str) -> str   # "acme-senior-backend-engineer.pdf"
```

The template is ATS-friendly: a single column, real text, no tables, no images, system fonts. Section headings in English or Portuguese.

CLI: `trampo resume --base [--lang en|pt]` writes `<private>/resumes/base-<lang>.pdf`.

- [x] Tests first:
  - `test_html_has_every_work_entry`.
  - `test_pt_headings` — "Experiência", "Formação", "Habilidades".
  - `test_no_tables_or_images`.
  - `test_filename_slug` — accents stripped, lowercase, hyphens.
  - `test_pdf_written` — the file starts with `%PDF` (needs `playwright install chromium`).
- [x] Implement; register the `--base` option; green checks.
- [x] 👤 Generate `base-en.pdf` from the owner's `private/resume.json` and ask the owner to review the layout.
- [x] Commit `feat(resume): render resumes to PDF`.

### Task 16: Tailored resume

Load the `claude-api` skill first.

**Files:** create `src/trampo/prompts/tailor.md`, `src/trampo/resume/tailor.py`, `tests/resume/test_tailor.py`; modify `src/trampo/pipeline.py`, `src/trampo/cli.py`

**Produces:**

```python
def invented_facts(base: Resume, tailored: Resume) -> list[str]   # empty list = OK
def tailor_params(item: JobWithPostings, resume: Resume, track: Track, prompt: Prompt,
                  model: str, lang: Literal["en", "pt"]) -> dict  # output_config.format = Resume schema
def save_tailored(store: Store, paths: Paths, job_id: int, tailored: Resume,
                  base: Resume, model: str, prompt: Prompt) -> Path    # guard → render → PDF → store.save_resume
def tailor_now(ctx: RunContext, job_id: int) -> Path               # regular call, used by the CLI and the web page
```

`invented_facts` flags: a `work[].name` not in the base; `startDate`/`endDate` differing from the base entry for that company; any number (`\d+(?:[.,]\d+)?`) in the tailored text that doesn't appear in the base text; a `skills[].keywords` entry not present in the base (case-insensitive). Reordering, omitting and rephrasing are allowed; so are translated headings and prose.

`tailor.md`: tailor the Base resume to the Job and its Track emphasis, use the Job's terms, never add facts, write in `lang` (`job_language`).

Pipeline hook (after the Digest): new `eligible` Jobs with `fit_score >= auto_resume_min_fit_score` and no `resume_path` → one `resume` batch (`custom_id = "resume-<job_id>"`), same polling and recovery as judging. Refused by the guard → log the flagged facts, nothing saved.

CLI: `trampo resume <job_id>` → `tailor_now`, prints the path.

- [ ] Tests first:
  - `test_guard_flags_new_company`, `test_guard_flags_changed_dates`, `test_guard_flags_new_number` (e.g. "40%" absent from the base) — Review Focus 4.
  - `test_guard_allows_reorder_and_translation`.
  - `test_refused_resume_not_saved`.
  - `test_auto_only_for_eligible_at_threshold` — Fit score 7 and `needs_review` Jobs are skipped; 8 gets one.
  - `test_resume_batch_recovered_next_run`.
- [ ] Implement; green checks; commit `feat(resume): Tailored resumes`.

### Task 17: Web page (`trampo serve`)

**Files:** create `src/trampo/web/__init__.py`, `src/trampo/web/templates/{base,index,_job_row}.html`, `src/trampo/web/static/htmx.min.js`, `src/trampo/web/static/pico.min.css`, `tests/web/test_app.py`; modify `src/trampo/cli.py`

**Produces:**

```python
def create_app(store: Store, paths: Paths, tailor: Callable[[int], Path],
               now: Callable[[], datetime]) -> FastAPI
```

Routes (all UI text in pt-BR):

- `GET /` — the Job list with filters (`status`, `track`, `company`, `since`). Jobs created after `meta.last_visit` are highlighted, then `last_visit` is set to now.
- `POST /jobs/{id}/status` (form `status`) → returns the updated `_job_row` fragment for htmx.
- `POST /jobs/{id}/notes` (form `notes`).
- `POST /jobs/{id}/override` (form `verdict`, `reason`) → `store.override_verdict`.
- `POST /jobs/{id}/resume` → `tailor(id)`, returns the row with the PDF link.
- `GET /resumes/{filename}` → serves only files directly inside `paths.resumes_dir`; anything else → 404.

Vendor pinned versions of htmx and Pico.css into `static/` (both permissively licensed; keep their license headers). CLI: `trampo serve [--port 8765]` runs uvicorn on host `127.0.0.1`.

- [ ] Tests first (FastAPI `TestClient`):
  - `test_index_lists_jobs_in_portuguese`.
  - `test_filters`.
  - `test_status_change_persists`.
  - `test_override_creates_override_row`.
  - `test_new_since_last_visit_highlighted_once`.
  - `test_resume_path_traversal_blocked` — `/resumes/..%2Fprofile.toml` → 404.
  - `test_serve_binds_localhost`.
- [ ] Implement; green checks.
- [ ] 👤 Run `trampo serve` on the owner's data and ask for feedback on the page.
- [ ] Commit `feat(web): local Job page`.

### Task 18: Backup

**Files:** create `src/trampo/backup.py`, `tests/test_backup.py`; modify `src/trampo/pipeline.py`

**Produces:** `def backup_private(paths: Paths, store: Store, dest: Path) -> None` — copies every file in the private dir except the database (`shutil.copytree`, `dirs_exist_ok=True`) and writes the database with `store.backup_to(dest / "trampo.db")`. Called at the end of each Run when `profile.backup_dir` is set. A backup failure is logged and never fails the Run.

- [ ] Tests first: `test_copies_files_and_db`, `test_db_copy_readable_while_open`, `test_skipped_without_backup_dir`, `test_failure_does_not_fail_run`.
- [ ] Implement; green checks; commit `feat(backup): copy private data after each Run`.

### Task 19: Eval (`trampo eval`)

Load the `claude-api` skill first.

**Files:** create `src/trampo/evaluate.py`, `tests/test_evaluate.py`; modify `src/trampo/cli.py`

**Produces:**

```python
@dataclass
class EvalReport:
    total: int; agree: int
    confusion: dict[tuple[str, str], int]           # (override verdict, new verdict) -> count
    disagreements: list[tuple[int, str, str]]        # (job_id, expected, got)

def evaluate(client: anthropic.Anthropic, store: Store, config: Config, profile: Profile,
             resume: Resume, model: str) -> EvalReport
```

It re-judges every overridden Job with the current prompt and model (regular calls) and compares the result to the Override's `to_verdict`. CLI: `trampo eval [--yes]` prints how many calls it will make and asks for confirmation (it costs money) unless `--yes`; then prints the agreement rate, the confusion table, the disagreements, the model ID and the prompt hash.

- [ ] Tests first: `test_agreement_rate`, `test_confusion_counts`, `test_no_overrides_is_empty_report`, `test_cli_asks_confirmation`.
- [ ] Implement; green checks; commit `feat(eval): measure the judge against Overrides`.

### Task 20: Go-live 👤

**Files:** modify `README.md` ("Running it" section); create `private.example/.env.example`

- [ ] 👤 The owner creates an Anthropic API key and sets the workspace **spend limit to US$ 30/month** in the Console.
- [ ] 👤 The owner creates a Telegram bot with @BotFather and gets the chat ID; fills in `private/.env` (`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
- [ ] Write `private.example/.env.example` with the three names and empty values.
- [ ] README "Running it": `uv sync`, `uv run playwright install chromium`, copying `private.example/` to `private/`, the `.env` variables, the commands, and the Windows Task Scheduler setup. Get the distro name from `wsl -l -v`; `bash -lc` puts uv on PATH. In PowerShell:

  ```powershell
  $run = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d <distro> --cd ~/projects/trampo -- bash -lc 'uv run --env-file private/.env trampo run'"
  Register-ScheduledTask -TaskName "trampo-run" -Action $run `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 8am) `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)

  $serve = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d <distro> --cd ~/projects/trampo -- bash -lc 'uv run --env-file private/.env trampo serve'"
  Register-ScheduledTask -TaskName "trampo-serve" -Action $serve -Trigger (New-ScheduledTaskTrigger -AtLogOn)
  ```

- [ ] 👤 First real Run by hand with the owner watching: Discovery finds Boards, the Digest arrives, the page shows the Jobs, and a Tailored resume PDF looks right.
- [ ] 👤 The owner sets `[backup] dir` in `private/profile.toml`.
- [ ] Green checks, commit `docs: setup and scheduling`.
- [ ] 👤 When the owner says so: create the public GitHub repo and push.

---

## Phase 2 — Assisted apply (outline)

Before starting phase 2, run a grilling session to specify it in detail (form field mapping per ATS, how open questions are answered, how submission is detected). Constraints are already set by [ADR 0002](docs/adr/0002-assisted-apply-no-ats-submit-api.md) and the "Assisted apply" section of docs/product.md.

- **Task 21: Visible browser and form filling** — Playwright headed Chromium through WSLg; per-ATS fill of the fixed fields and the Tailored resume upload; never clicks submit.
- **Task 22: Open questions and cover letter** — Claude drafts answers and a cover letter only from the Base resume; the same `invented_facts` guard applies.
- **Task 23: Apply button and confirmation** — the web page's **Apply** button starts Assisted apply; detecting the ATS confirmation page sets Status `applied`.
