# Product

Domain terms (Posting, Job, Verdict, Status, Override, Track, Run, Digest…) are defined in [CONTEXT.md](../CONTEXT.md). How it is built lives in [architecture.md](architecture.md).

## Idea

A personal agent that finds remote software engineering jobs a Candidate living in Brazil can actually take, without combing through dozens of job boards by hand every week.

Inspired by the video [5 Truques para Achar Vagas Gringas Escondidas (FORA DO LINKEDIN) — Ana Neri](https://www.youtube.com/watch?v=X46o4tA531Y) (pt-BR).

## Goal

Every day, pull open Postings from the public APIs of the main ATSs (Ashby, Greenhouse, Lever), judge each Job against the Candidate's profile and eligibility (remote from Brazil), and deliver only the Jobs that are **new** since the last Run, already judged, with a Digest on Telegram. For the best matches, generate a Tailored resume.

## Candidate profile

The profile is private (see [Privacy and repository](#privacy-and-repository)):

- **Base resume** (`private/resume.json`): passed to Claude when judging Jobs, and the source of every Tailored resume.
- **Preferences** (`private/profile.toml`): Salary floor per contract type and accepted contract types.

Search target:

- Backend engineer — aim for Senior, accept Mid.
- Also Agent Engineer / AI Agent Engineer and Automation Engineer roles.
- Based in Brazil (UTC-3), advanced English.
- Accepted contracts: CLT, PJ or international contractor.

### Tracks

Every Job is assigned to one Track. The Fit score is relative to that Track, and the Tailored resume emphasizes different things in each.

| Track | Title keywords | Tailored resume emphasizes |
| --- | --- | --- |
| **Backend** | `backend`, `back-end`, `golang`, `go engineer`, `software engineer` (when the description mentions Go) | Go, distributed systems, Kafka, PostgreSQL, fintech |
| **Agents/Automation** | `agent engineer`, `ai agent engineer`, `agent ai engineer`, `automation engineer` | day-to-day AI tooling, this project, backend as the foundation |

**Ignore** titles containing: `frontend`, `mobile`, `ios`, `android`, `data scientist`, `manager`, `intern`, `junior`.

## Eligibility

**Include** Jobs that are:

- Open and published in the last 7 days. When a Posting has no reliable publish date, the date of the Run that first saw it is used instead (with daily Runs the error is at most one day; only the very first Run may surface old Jobs).
- Fully remote and open to people living in Brazil: Brazil, LATAM, the Americas or worldwide.
- Matching the keywords of one Track.

**Reject** Jobs that:

- Require US work authorization.
- Are hybrid or on-site.
- Restrict location to countries the Candidate can't work from ("US only", "EU only", "must reside in…").
- Publish a salary range below the Salary floor.
- Are "Automation Engineer" in the QA/test-automation sense (the title is ambiguous; Claude decides from the description).

**Verdict** values:

- `eligible` — passes every rule;
- `needs_review` — ambiguous, the Candidate decides (e.g. "LATAM preferred", EU time zone required, "Remote (US)" without saying whether work authorization is needed, unclear EOR/Deel hiring);
- `rejected` — always with the reason, so the Candidate can audit the rules;
- `pending` — not judged yet (e.g. the spend cap was hit).

## A Run

1. **Discovery** — the agent builds and maintains the list of Boards to track by itself:
   - web searches that combine ATS domains with Track keywords, e.g. `site:jobs.ashbyhq.com remote LATAM`, `site:job-boards.greenhouse.io golang remote`, `site:jobs.lever.co "ai agent" remote`;
   - at most **10 Discovery searches per day** (searches cost money; reading ATS APIs is free);
   - every slug is validated against the ATS API before being added;
   - **no cap** on the number of Boards; a Board leaves the list only when it no longer exists.
2. **Collect** Postings from each Board's public API:
   - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`
   - Lever: `https://api.lever.co/v0/postings/{slug}?mode=json`
   - Ashby: `https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`
3. **Pre-filter (code)** — drop what is objective: publish date, title keywords, and the structured workplace field (hybrid/on-site) when the ATS provides it; location is left to Claude.
4. **Dedupe** — group Postings into Jobs before judging, so each Job is judged once. Two Postings belong to the same Job when:
   - they share ATS + posting ID; or
   - they share company + normalized title. Postings of one Job in several locations become a single Job with a list of locations. A Repost (new ID within 30 days) keeps the Job's Status and notes.
5. **Judge (Claude)** — for new Jobs, the latest Claude Opus reads the full description and returns structured output: Track, Verdict + reason, remote?, open to Brazil/LATAM?, requires US work authorization?, and a Fit score (0–10) with a short justification.
6. **Output** — store results, refresh the page, send the Digest.
7. **Tailored resume** — written to `private/resumes/<company>-<title>.pdf`:
   - **automatically** for new `eligible` Jobs with Fit score ≥ 8 (adjustable); `needs_review` Jobs never get one automatically;
   - **on demand** for any other Job the Candidate picks;
   - starts from the Base resume and reorders/emphasizes summary, experience and skills for the Job and its Track, using the Job's own terms;
   - **never** adds experience, technology, title or number that isn't in the Base resume;
   - in the Job's language: the Base resume is in English and Claude translates it for Brazilian Jobs;
   - ATS-friendly **PDF**: single column, selectable text, no tables or images.

## Schedule and cost

- **Daily at 08:00 (Brasília)**, plus a manual command. If the PC is off at 08:00, the Run happens when it's turned on.
- **Model:** always the latest Claude Opus.
- **Spend cap:** **US$ 30/month**. When it is hit:
  - judging stops and collected Jobs stay `pending`;
  - the Candidate gets a Telegram alert;
  - once the cap resets, the next Run judges every `pending` Job **still open** on its ATS, even outside the 7-day window; Jobs closed in the meantime become `rejected` with the reason "job closed".
- Expected cost: US$ 20–40/month. Google Alerts can't trigger the agent; at most it could become an extra source (RSS) later.

## Outputs

- **Job history**, stored locally and used for deduplication.
- **Local web page** (HTMX, UI in Portuguese) with:
  - company, title, Track, Posting link, locations/workplace, publish date, salary range when published;
  - Verdict with reason and Fit score;
  - Status set by the Candidate: `new` → `seen` → `applied` → `dismissed` (in phase 1, `applied` is set by hand);
  - free-text notes per Job;
  - link to the Tailored resume when it exists, and a button to generate one on demand;
  - highlight of Jobs that are new since the last visit;
  - filters by Status, Track, company and date;
  - an **Apply** button (phase 2).
- **Digest** on Telegram, sent only when a Run finds at least one new `eligible` Job:
  - per Job: company, title, Track, Fit score and the direct Posting link;
  - a final line with how many Jobs went to `needs_review` and how many were `rejected`.
- **Overrides as labeled examples** — when the Candidate replaces a Verdict (dismisses an `eligible` Job, rescues a `rejected` one), the Override is stored with its reason. In phase 1 the agent doesn't learn from them automatically; they form an evaluation set to measure the rules and tune the prompts.

## Assisted apply (phase 2)

The ATS submit APIs only accept credentials issued by the hiring company, and the hosted forms carry CAPTCHAs, so applying can't be fully automatic — see [ADR 0002](adr/0002-assisted-apply-no-ats-submit-api.md). Assisted apply instead:

1. The Candidate clicks **Apply** on the page.
2. A visible browser opens the Job's form, fills the fixed fields (name, email, phone, LinkedIn, GitHub), attaches the Tailored resume and drafts answers to open questions from the Base resume only.
3. If the form has a **cover letter** field, Claude drafts one from the Base resume and the Job.
4. The Candidate reviews, solves any CAPTCHA and submits.
5. The agent detects the confirmation page and sets the Job's Status to `applied`.

## Phases

- **Phase 1:** Discovery, collect, pre-filter, judge, dedupe, web page (list, filters, Status, notes), Tailored resume PDFs and the Digest.
- **Phase 2:** Assisted apply and cover letters.

## Privacy and repository

- **Public repository** on GitHub, as a portfolio piece.
- **Language:** English for code, commits and docs; Portuguese for the web page and Telegram messages.
- **Base resume source:** the original PDF is converted once into a structured file ([JSON Resume](https://jsonresume.org/schema)) reviewed by the Candidate. It becomes the source of truth, and every PDF — the base one included — is generated from it.
- **Never committed** (`private/`): Base resume and original PDF, contact data, Salary floor, generated resumes, the database, logs and secrets.
- **Public:** keywords, Tracks and eligibility rules — they show how the product thinks.
- **For people cloning the repo:** `private.example/` holds a fictional Candidate ("Alex Silva, Backend Engineer") with a Base resume, fake contact data and sample preferences, enough to run the whole flow.

## Out of scope (for now)

- Submitting an application without the Candidate's final review.
- Tracking hiring stages (interview, offer, rejection).
- Scraping LinkedIn, Indeed and similar sites.
- Other ATSs (Workable, SmartRecruiters…).
