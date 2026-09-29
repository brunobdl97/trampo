# Trampo

Personal job-search agent: a daily pipeline pulls Postings from ATS public APIs, Claude judges each Job, a Digest goes to Telegram, and Tailored resumes are rendered to PDF. Python 3.14, run locally on WSL2.

## Read first

- [docs/product.md](docs/product.md) — what it does and why
- [docs/architecture.md](docs/architecture.md) — stack, layout, runtime, dependencies
- [CONTEXT.md](CONTEXT.md) — domain glossary; use these terms in code, identifiers and commits
- [docs/adr/](docs/adr/) — decisions not to relitigate

## Commands

```bash
uv sync                                   # install deps
uv run playwright install chromium        # once, for PDF rendering
uv run pytest
uv run pyright
uv run ruff check . && uv run ruff format --check .
uv run --env-file private/.env trampo run   # also: serve | resume <job> | eval
```

ruff format + fix runs automatically on every edited `.py` file (hook in `.claude/settings.json`).

## Rules

- Never commit, print or paste the contents of `private/` (Base resume, contact data, Salary floor, DB, generated resumes, `.env`). Examples go in `private.example/` with fictional data.
- Tailored resumes and cover letters never contain facts absent from the Base resume.
- Tests never call real APIs: use `httpx.MockTransport` with recorded responses in `tests/fixtures/`.
- Claude API: resolve the newest Opus at runtime via the Models API and use only features stable across Opus versions (adaptive thinking, `output_config.format` structured output, no prefill, no forced `tool_choice`). Load the `claude-api` skill, when available, before touching Claude calls.
- Store the model ID and prompt hash on every Verdict and Tailored resume.
- No new dependency without the owner's approval; the approved list is in docs/architecture.md.
- A task is done only when `uv run pyright` and `uv run pytest` pass.
- English for code, comments, commits and docs; Portuguese (pt-BR) for the web UI and Telegram messages.

## Conventions

- One module per feature under `src/trampo/`; a `Protocol` only where there is more than one implementation (ATS clients).
- Prompts live in `src/trampo/prompts/*.md`, never as inline strings.
- All SQL lives in `store.py`; schema in `schema.sql`; migrations via `PRAGMA user_version`.
