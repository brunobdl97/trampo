# Architecture

What the product does is in [product.md](product.md); domain terms are in [CONTEXT.md](../CONTEXT.md); decisions that are hard to reverse are in [adr/](adr/).

## Shape

A Run is a deterministic pipeline in code; Claude is called only at specific steps (Discovery, judging, Tailored resumes, cover letters) — see [ADR 0001](adr/0001-deterministic-pipeline-not-agent-loop.md).

```
Discovery ─► collect Postings ─► pre-filter ─► dedupe into Jobs ─► judge (Batch) ─► Digest
                                                                                  └─► Tailored resumes (Batch)
```

Everything runs locally on the Candidate's PC (WSL2).

## Stack

| Concern | Choice |
| --- | --- |
| Language | Python 3.14 |
| Env, deps, lockfile, runner | uv |
| Lint + format | ruff |
| Types | pyright (same engine as VS Code's Pylance) |
| Tests | pytest + `httpx2.MockTransport` |
| Claude | `anthropic` SDK |
| HTTP (ATS, Telegram) | `httpx2` |
| Data models | Pydantic (config, Base resume, Claude structured output) |
| Web | FastAPI + Jinja2 + uvicorn, htmx + Pico.css vendored as static files |
| PDF (and phase-2 Assisted apply) | Playwright + Chromium |
| Storage | SQLite via stdlib `sqlite3` — see [ADR 0003](adr/0003-plain-sqlite-no-orm.md) |
| Config | TOML via stdlib `tomllib` |
| CLI | stdlib `argparse` |

### Dependencies

| Package | Why |
| --- | --- |
| `anthropic` | Models API, Batches, web search, structured output |
| `httpx2` | ATS clients, Telegram (the HTTP library `anthropic` 1.x is built on; declared because we import it) |
| `pydantic` | config, Base resume, Claude output (same) |
| `fastapi`, `uvicorn` | web page server |
| `jinja2` | page and resume templates |
| `python-multipart` | lets FastAPI read the form posts htmx sends |
| `playwright` | HTML → PDF now, Assisted apply in phase 2 |
| dev: `pytest`, `ruff`, `pyright` | tests, lint/format, types |

Everything else comes from the standard library (`sqlite3`, `tomllib`, `argparse`, `logging`, `hashlib`). New dependencies are discussed before being added.

## Layout

```
config.toml                # public: Tracks, keywords, thresholds
src/trampo/
  cli.py                   # argparse entry point: run | serve | resume | eval
  config.py                # Config, Profile, Paths
  models.py                # domain types shared by every module
  store.py  schema.sql     # all SQL
  ats/                     # Greenhouse, Lever, Ashby clients behind one Protocol
  prefilter.py  dedup.py
  claude.py                # client, newest Opus, prompts, batches
  judge.py  discovery.py  digest.py
  pipeline.py              # one Run end to end
  resume/                  # model, HTML → PDF rendering, Tailored resume
  web/                     # FastAPI app, templates/, static/
  backup.py  evaluate.py
  prompts/                 # *.md prompt files
tests/
  fixtures/                # recorded ATS / Claude / Telegram responses
private/                   # gitignored — see below
private.example/           # same shape, fictional Candidate
```

The file-by-file map and the build order are in [backlog.md](../backlog.md).

One module per feature. A `Protocol` exists only where there is more than one implementation (the ATS clients).

## CLI and scheduling

`trampo` is one entry point (`[project.scripts]` in `pyproject.toml`), always run through uv so secrets load from `private/.env`:

```
uv run --env-file private/.env trampo run            # one Run
uv run --env-file private/.env trampo serve          # web page on 127.0.0.1
uv run --env-file private/.env trampo resume <job>   # Tailored resume from the terminal
uv run --env-file private/.env trampo eval           # measure the agent against Overrides (costs money)
```

Windows Task Scheduler starts both inside WSL:

- `run` daily at 08:00 BRT, with "run task as soon as possible after a scheduled start is missed";
- `serve` at logon.

## Claude usage

- **Model:** at the start of each Run, the newest Opus is resolved through the Models API. Only features stable across Opus versions are used: adaptive thinking, structured output via `output_config.format` (Pydantic models), no assistant prefill, no forced `tool_choice`.
- **Traceability:** every Verdict and Tailored resume stores the model ID and a hash of the prompt file used, so `trampo eval` can compare prompt and model versions against the Overrides.
- **Prompts** live in `src/trampo/prompts/*.md`, not inline strings.
- **Caching:** the Base resume and instructions form a stable, cached prefix.
- **Discovery** uses Claude's server-side web search tool restricted (`allowed_domains`) to the ATS domains, at most 10 searches a day.
- **Batches:** judging and automatic Tailored resumes go through the Batch API (50% cheaper):
  1. `run` submits the judging batch and polls every ~30 s until it ends (usually minutes, at most 24 h);
  2. the batch ID is stored, so if the process dies (PC off), the next `run` first collects the open batch;
  3. the Digest is sent right after judging;
  4. automatic Tailored resumes go in a second batch that doesn't delay the Digest;
  5. on-demand resumes (page button or `trampo resume`) use a regular call, since the Candidate is waiting.
- **Spend cap:** US$ 30/month, enforced by a workspace spend limit in the Anthropic Console. When the API reports the limit, judging stops, Jobs stay `pending`, and a Telegram alert goes out.

## Storage

One SQLite file, `private/trampo.db`, accessed only from `store.py` with hand-written SQL. The schema lives in `schema.sql`; migrations are versioned with `PRAGMA user_version`. A `runs` table records each Run's start, end, counts and error.

## Resumes and PDF

The Base resume follows the [JSON Resume](https://jsonresume.org/schema) schema. Claude returns Tailored resumes in the same schema (structured output), and a single Jinja2 HTML template renders both base and tailored versions; Playwright turns the HTML into an ATS-friendly PDF (single column, selectable text).

## Private data and secrets

```
private/                   # gitignored
  resume.json              # Base resume (includes contact data)
  profile.toml             # Salary floor, accepted contracts, backup folder
  .env                     # ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  resumes/                 # generated PDFs
  trampo.db
  logs/
  CV_*.pdf                 # original resume, reference only
```

`private.example/` mirrors this with a fictional Candidate. Secrets are loaded by `uv run --env-file`; no dotenv library.

## Observability and backup

- Daily log file in `private/logs/` (stdlib `logging`).
- A failed Run sends a Telegram alert with a short error.
- At the end of each Run, `private/` except `.env` is copied to the cloud-synced Windows folder set in `profile.toml`; the database is copied with SQLite's online backup API, which is safe while the DB is in use, as a rollback-journal file (no WAL side files).

## Testing

- pytest; TDD.
- All HTTP (ATS clients, Telegram, and the Anthropic SDK, which uses httpx2) goes through `httpx2.MockTransport` serving real recorded responses from `tests/fixtures/`.
- Tests never call real APIs. Prompt quality is measured with `trampo eval`, not unit tests.
- CI (GitHub Actions) runs ruff, pyright and pytest on every push and pull request.

## Phase 2: Assisted apply

Playwright drives a **visible** Chromium through WSLg to fill the ATS-hosted form; the Candidate reviews and submits. See [ADR 0002](adr/0002-assisted-apply-no-ats-submit-api.md).
