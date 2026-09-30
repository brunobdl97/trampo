# trampo

A personal job-search agent. Every morning it pulls fresh postings from the public job boards of companies on Greenhouse, Lever and Ashby, has Claude judge which remote roles an engineer living in Brazil can actually take, sends a Telegram digest, and generates an ATS-friendly tailored resume (PDF) for the best matches.

> **Status:** phase 1 implemented; Assisted apply is phase 2.

## How it works

1. **Discovery** — web searches find new company job boards on the three ATSs.
2. **Collect** — each board's public API is read (no scraping).
3. **Pre-filter** — date, title keywords and location fields drop the obvious misses.
4. **Dedupe** — the same job posted in several locations, or reposted, stays one job.
5. **Judge** — the latest Claude Opus reads each remaining job and returns a verdict (`eligible` / `needs_review` / `rejected`, with a reason), a track and a 0–10 fit score.
6. **Digest** — new eligible jobs go to Telegram; everything is browsable on a local htmx page.
7. **Tailored resume** — for strong matches, a resume rewritten for the job from the candidate's base resume, never adding facts.

Applying stays human-in-the-loop: ATS submit APIs only accept employer credentials and their forms carry CAPTCHAs ([why](docs/adr/0002-assisted-apply-no-ats-submit-api.md)).

## Docs

- [Product](docs/product.md)
- [Architecture](docs/architecture.md)
- [Domain glossary](CONTEXT.md)
- [Architecture decision records](docs/adr/)

## Running it

1. **Requirements:** WSL2 (or Linux) and [uv](https://docs.astral.sh/uv/).

   ```bash
   uv sync
   uv run playwright install --with-deps chromium
   ```

2. **Private data** lives in a gitignored `private/` folder, seeded from `private.example/`:

   ```bash
   cp -r private.example private
   ```

   - Replace `private/resume.json` with your own Base resume (a [JSON Resume](https://jsonresume.org/schema) subset).
   - Edit `private/profile.toml`: Salary floor per contract type, accepted contracts, and `[backup] dir` — a cloud-synced folder to back up to after each Run; empty disables backup, and `.env` is never backed up.
   - Fill in `private/.env` (see Secrets below).

3. **Secrets** (`private/.env`, three variables — see `private.example/.env.example`):
   - `ANTHROPIC_API_KEY` — create a key in the Claude Console and set a **workspace spend limit** (the project assumes US$ 30/month).
   - `TELEGRAM_BOT_TOKEN` — create a bot with [@BotFather](https://t.me/BotFather).
   - `TELEGRAM_CHAT_ID` — send the bot a message, then read `chat.id` from `https://api.telegram.org/bot<token>/getUpdates`.

4. **Commands** (always through `uv run --env-file private/.env`):

   ```bash
   uv run --env-file private/.env trampo run                        # one Run
   uv run --env-file private/.env trampo serve                      # web page, http://127.0.0.1:8765
   uv run --env-file private/.env trampo resume --base [--lang pt]  # render the Base resume
   uv run --env-file private/.env trampo resume <job_id>            # Tailored resume for a Job
   uv run --env-file private/.env trampo eval [--yes]                # measure the judge against Overrides (costs money)
   ```

5. **Scheduling on Windows:** Task Scheduler starts both inside WSL2. Get `<distro>` from `wsl -l -v`; `bash -lc` puts `uv` on PATH.

   ```powershell
   $run = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d <distro> -- bash -lc 'cd ~/projects/trampo && uv run --env-file private/.env trampo run'"
   Register-ScheduledTask -TaskName "trampo-run" -Action $run `
     -Trigger (New-ScheduledTaskTrigger -Daily -At 8am) `
     -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)

   $serve = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d <distro> -- bash -lc 'cd ~/projects/trampo && uv run --env-file private/.env trampo serve'"
   Register-ScheduledTask -TaskName "trampo-serve" -Action $serve -Trigger (New-ScheduledTaskTrigger -AtLogOn)
   ```

   `-StartWhenAvailable` runs a missed 08:00 Run as soon as the PC is next on.

   Check it once: `Start-ScheduledTask trampo-run`, then confirm a new line in `private/logs/`.

6. **Where things go:** `private/logs/YYYY-MM-DD.log`, `private/resumes/`, `private/trampo.db`.

## License

[MIT](LICENSE)
