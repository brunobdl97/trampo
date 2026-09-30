# trampo

A personal job-search agent. Every day it finds company job boards on Greenhouse, Lever and Ashby, pulls their open postings, has Claude judge which remote roles an engineer living in Brazil can actually take, sends the new matches to Telegram, and writes an ATS-friendly tailored resume (PDF) for the best ones. Everything is also browsable on a small local web page.

> **Status:** phase 1 implemented; Assisted apply is phase 2.

The agent's messages and web page are in Portuguese (pt-BR); code and docs are in English.

## Contents

- [How it works](#how-it-works)
- [What you get](#what-you-get)
- [What you need](#what-you-need)
- [Setup, step by step](#setup-step-by-step)
- [Your first Run](#your-first-run)
- [Run it every day (Windows)](#run-it-every-day-windows)
- [Day-to-day use](#day-to-day-use)
- [Costs](#costs)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Docs](#docs)

## How it works

A **Run** is one pass of a fixed pipeline. It is plain code; Claude is only called in steps 1, 5 and 7.

1. **Discovery.** Claude runs up to 10 web searches a day (`site:jobs.ashbyhq.com "backend" remote`, …) looking for company job boards on the three ATSs. Each board found is checked against the ATS API before it is kept.
2. **Collect.** Every known board is read through the ATS's free public API (no scraping).
3. **Pre-filter (code only, free).** Drops what is objective: titles outside the target (frontend, mobile, manager, junior, …), hybrid or on-site roles, and postings older than 7 days. Location is left to Claude.
4. **Dedupe.** The same job posted in several locations, or reposted within 30 days, becomes one **Job**, so it is judged only once.
5. **Judge.** The newest Claude Opus reads each new Job next to your resume and returns a **Verdict** — `eligible`, `needs_review` or `rejected`, always with a reason in Portuguese — plus a **Track** (Backend or Agents/Automation) and a **Fit score** from 0 to 10. Judging goes through Anthropic's Message Batches API: half the price, but results arrive when Anthropic processes the batch (usually minutes, sometimes hours, at most 24 h).
6. **Digest.** If at least one Job became `eligible`, a Telegram message lists them.
7. **Tailored resumes.** For each new `eligible` Job with Fit score ≥ 8, Claude rewrites your resume for that job — reordering and emphasizing, in the job's language — and a guard refuses any version that adds a company, title, date, number or skill that is not in your resume. Approved versions are rendered to PDF.
8. **Backup.** Your private folder (minus the secrets file) is copied to a cloud-synced folder.

The Run is built to survive a bad day: a board that is down is skipped without closing its jobs; if the Anthropic spend limit is hit, Jobs stay `pending`, you get one alert, and a later Run judges them; if the PC is turned off while a batch is processing, the next Run collects that batch instead of paying for it again.

Applying stays in your hands: ATS submit APIs only accept employer credentials and their forms carry CAPTCHAs ([why](docs/adr/0002-assisted-apply-no-ats-submit-api.md)).

## What you get

- **A Telegram Digest** when there are new eligible Jobs, e.g.:

  > **Trampo: 3 vaga(s) nova(s)**
  > • **Acme Pay** — [Senior Backend Engineer (Go)](#) · Backend · Fit 9/10
  > • …
  > 2 para revisar · 11 rejeitada(s)

  You also get a message if a Run fails or if the Anthropic spend limit is reached. A quiet day sends nothing.
- **A local web page** (`trampo serve`, http://localhost:8765) with every Job: company, title, Track, links, locations, workplace, publish date, salary when published, Verdict and reasons, Fit score. You mark the Status (`nova` → `vista` → `candidatada` / `descartada`), write notes, correct a Verdict (an **Override**, kept as a labeled example), and generate a tailored resume on demand. Jobs that are new since your last visit are highlighted.
- **Tailored resume PDFs** in `private/resumes/<company>-<title>.pdf`, single column with selectable text so ATS parsers read them.
- **History** in a local SQLite database (`private/trampo.db`) and a daily log in `private/logs/`.

## What you need

- **Windows 11 with WSL2** (Ubuntu), or any Linux. Scheduling instructions below are for Windows.
- **[uv](https://docs.astral.sh/uv/)** — it installs Python 3.14 and every dependency for you.
- **An Anthropic account** with an API key and **prepaid credits** ([Claude Console](https://platform.claude.com)).
- **A Telegram account** (for the Digest).
- **Your resume**, which you will convert once into a JSON file (see step 4).

## Setup, step by step

### 1. WSL2 and uv

On Windows, open **PowerShell as administrator** and install WSL with Ubuntu (skip if you already have it), then restart:

```powershell
wsl --install -d Ubuntu
```

Inside Ubuntu, install uv and reopen the terminal:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone and install

The scheduling commands below assume the project lives in `~/projects/trampo`; adjust them if you clone elsewhere.

```bash
mkdir -p ~/projects && cd ~/projects
git clone <this repository's URL> trampo
cd trampo
uv sync                                          # Python 3.14 + dependencies
uv run playwright install --with-deps chromium   # browser used to print PDFs (asks for sudo)
```

### 3. Create your private folder

Everything personal lives in `private/`, which git ignores. Start from the fictional example:

```bash
cp -r private.example private
cp private.example/.env.example private/.env
```

### 4. Your resume — `private/resume.json`

This is your **Base resume**: the single source of truth that the judge reads and every tailored resume starts from. It uses a subset of the [JSON Resume](https://jsonresume.org/schema) format; open `private.example/resume.json` (a fictional "Alex Silva") to see every field:

- `basics`: `name`, `label` (headline), `email`, `phone`, `summary`, `location` (`city`, `countryCode`), `profiles` (`network`, `url`).
- `work`: one entry per position — `name` (company), `position`, `startDate`, `endDate` (omit for your current job), `summary`, `highlights`.
- `education`, `skills` (`name` + `keywords`), `languages`, `projects`.
- Dates are `YYYY-MM` or `YYYY-MM-DD`.

Write it in English; tailored resumes are translated to Portuguese when a job is in Portuguese. Keep it honest and complete: a tailored resume can only reuse facts that are in this file. Then render it once to check that it loads and looks right:

```bash
uv run trampo resume --base            # writes private/resumes/base-en.pdf
uv run trampo resume --base --lang pt  # same content, Portuguese headings
```

### 5. Your preferences — `private/profile.toml`

```toml
accepted_contracts = ["clt", "pj", "international_contractor"]

# Minimum MONTHLY pay per contract type. A job that publishes a lower range is rejected;
# a job that publishes no salary is never rejected for salary.
[salary_floor]
clt_brl = 12000
pj_brl = 15000
international_usd = 4000

[backup]
# A cloud-synced Windows folder seen from WSL; "" disables the backup.
dir = "/mnt/c/Users/<you>/OneDrive/trampo-backup"
```

The backup copies everything in `private/` except `.env`, so your keys never go to the cloud.

### 6. Anthropic API key, credits and spend limit

In the [Claude Console](https://platform.claude.com):

1. **API Keys** → create a key.
2. **Credits** → add credits. The API spends this prepaid balance; when it reaches zero, calls fail.
3. **Settings → Billing → Spend limits** (or the limits of the workspace the key belongs to) → set a **spend limit**. The project is designed around **US$ 30/month**. When the limit is reached, the Run keeps the remaining Jobs `pending`, sends one Telegram alert, and judges them after the limit renews.

### 7. Telegram bot and chat ID

1. In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot` and follow the prompts. It gives you the **bot token**.
2. Open the chat with **your new bot**, press **Start** and send any message (e.g. `hi`). The bot can only message you after this.
3. In a browser, open `https://api.telegram.org/bot<TOKEN>/getUpdates` and find `"chat":{"id":123456789,…}`. That number is your **chat ID**.

If the page shows `{"ok":true,"result":[]}`, the bot has not received your message yet: send one and reload (Telegram keeps these updates for 24 h). As a shortcut, [@userinfobot](https://t.me/userinfobot) replies with your ID, which is the same number for a private chat.

### 8. Fill in `private/.env`

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

Never commit or share this file. Every command that talks to an API reads it through `uv run --env-file private/.env`.

### 9. (Optional) Tune what it searches for — `config.toml`

`config.toml` is public and holds the search rules: the two Tracks and their title keywords, the words that discard a title, the 7-day job window, the 30-day repost window, the Fit score that triggers an automatic tailored resume (8) and the daily Discovery budget (10 searches). Change them to fit your own search.

## Your first Run

```bash
uv run --env-file private/.env trampo run
```

What to expect:

1. **Discovery** — 10 Claude web searches, a few minutes. The first day typically finds dozens of boards.
2. **Collect, pre-filter, dedupe** — a minute or two; free.
3. **Judge** — every new Job goes into one batch and the Run checks it every 30 seconds until Anthropic finishes it. **This can take from minutes to hours.** You can watch it in the Console under **Build → Batches**.
4. When the batch ends, the Verdicts are saved, the Digest arrives on Telegram (if any Job is eligible), tailored resumes are written for the strong matches, and the backup runs.

You do not have to keep the terminal open while the batch processes. If you stop the Run (Ctrl+C, closing the window, turning the PC off), the batch keeps processing at Anthropic, and the **next** `trampo run` collects it without resubmitting or paying again. Discovery also won't repeat on the same day: its 10 searches are a daily budget.

Afterwards, open the page to see every Job, not only the eligible ones:

```bash
uv run --env-file private/.env trampo serve   # then open http://localhost:8765
```

## Run it every day (Windows)

Windows Task Scheduler starts the Run inside WSL. With `-StartWhenAvailable`, the Run happens at 08:00 if the PC is on, or **as soon as you turn it on and log in** if it was off at 08:00 — once a day.

Find your distro name (usually `Ubuntu`):

```powershell
wsl -l -v
```

Then, in a normal (non-admin) **PowerShell**, replace `<distro>` and paste:

```powershell
$run = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d <distro> -- bash -lc 'cd ~/projects/trampo && uv run --env-file private/.env trampo run'"
Register-ScheduledTask -TaskName "trampo-run" -Action $run `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 8am) `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```

Optionally, start the web page every time you log in:

```powershell
$serve = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d <distro> -- bash -lc 'cd ~/projects/trampo && uv run --env-file private/.env trampo serve'"
Register-ScheduledTask -TaskName "trampo-serve" -Action $serve -Trigger (New-ScheduledTaskTrigger -AtLogOn)
```

`bash -lc` loads your shell profile so `uv` is on the PATH. Test it once:

```powershell
Start-ScheduledTask trampo-run
```

and check that a new line appeared in `private/logs/`. While a Run is working, a terminal window may be open (it can stay a while waiting for the batch); minimize it. If it gets closed, the next Run picks up where it stopped.

## Day-to-day use

| Command | What it does |
| --- | --- |
| `uv run --env-file private/.env trampo run` | One Run (the scheduler does this daily). |
| `uv run --env-file private/.env trampo serve [--port 8765]` | The local page, bound to 127.0.0.1 only. |
| `uv run --env-file private/.env trampo resume <job_id>` | Tailored resume for one Job, right now (a regular API call). The Job id is on the page. |
| `uv run trampo resume --base [--lang en\|pt]` | Render your Base resume to `private/resumes/base-<lang>.pdf`. |
| `uv run --env-file private/.env trampo eval [--yes]` | Re-judge every Job you overrode with the current prompt and model and report how often Claude agrees with you. Costs money; asks first unless `--yes`. |

On the page:

- **Status** is your own progress on a Job; set it freely.
- **Corrigir Verdict** (Override) records that the agent got a Job wrong, with your reason. Overrides don't change the agent automatically; they are the evaluation set for `trampo eval`.
- **Gerar currículo** makes a tailored resume on demand for any Job. **Read every tailored resume before sending it**: the guard catches invented companies, titles, dates, numbers and skills, but not every rewording.

Where things live:

```
private/
  resume.json      your Base resume
  profile.toml     Salary floor, contracts, backup folder
  .env             API keys (never backed up, never committed)
  trampo.db        Jobs, Verdicts, Status, notes, Overrides, Runs
  resumes/         base and tailored PDFs
  logs/            one log file per day (UTC date)
```

## Costs

- Reading the ATS APIs is free. Money goes to Claude: Discovery (up to 10 Opus web searches a day), judging (Batch API, 50% off) and tailored resumes.
- The project expects roughly **US$ 20–40/month** and is designed around a **US$ 30/month** workspace spend limit. After your first Run, check **Analytics → Cost** in the Console; if Discovery weighs too much, lower `discovery_max_searches_per_day` in `config.toml`.
- The spend limit is a ceiling; what is actually spent is your **prepaid credit balance**. Keep it topped up (or enable auto-reload), or Runs will fail with a "credit balance is too low" error.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `getUpdates` returns `{"ok":true,"result":[]}` | Send a message to your bot first (step 7), then reload. |
| The Run waits a long time at "judge batch" | Normal for the Batch API. Check **Build → Batches** in the Console. You can stop the Run; the next one collects the batch. |
| `trampo run` exits saying a variable is missing | Fill that variable in `private/.env`. |
| Telegram message "a execução falhou" | Read the day's file in `private/logs/`. A "credit balance is too low" error means your prepaid credits ran out. |
| Telegram message about the spend limit | The monthly limit was reached; Jobs stay pending and are judged after it renews. Raise the limit in the Console if you want them sooner. |
| PDF rendering fails (Chromium errors) | Run `uv run playwright install --with-deps chromium` again. |
| The scheduled task never runs | Check the distro name and the `~/projects/trampo` path in the task, look at the task's history in Task Scheduler, and test with `Start-ScheduledTask trampo-run`. |
| The page doesn't open | Is `trampo serve` running? If port 8765 is taken, use `--port`. |
| `trampo resume --base` fails | `private/resume.json` is invalid; the error names the field (dates must be `YYYY-MM` or `YYYY-MM-DD`). |

## Development

```bash
uv run pytest                                      # tests (no network: every HTTP call is mocked)
uv run pyright                                     # types
uv run ruff check . && uv run ruff format --check .
```

Tests never call real APIs and never read `private/`; recorded responses live in `tests/fixtures/`. See [CLAUDE.md](CLAUDE.md) for the project rules.

## Docs

- [Product](docs/product.md) — what it does and why
- [Architecture](docs/architecture.md) — stack, layout, runtime
- [Domain glossary](CONTEXT.md)
- [Architecture decision records](docs/adr/)

## License

[MIT](LICENSE)
