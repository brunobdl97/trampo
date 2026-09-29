# trampo

A personal job-search agent. Every morning it pulls fresh postings from the public job boards of companies on Greenhouse, Lever and Ashby, has Claude judge which remote roles an engineer living in Brazil can actually take, sends a Telegram digest, and generates an ATS-friendly tailored resume (PDF) for the best matches.

> **Status:** design complete, implementation starting.

## How it works

1. **Discovery** — web searches find new company job boards on the three ATSs.
2. **Collect** — each board's public API is read (no scraping).
3. **Pre-filter** — date, title keywords and location fields drop the obvious misses.
4. **Judge** — the latest Claude Opus reads each remaining job and returns a verdict (`eligible` / `needs_review` / `rejected`, with a reason), a track and a 0–10 fit score.
5. **Dedupe** — the same job posted in several locations, or reposted, stays one job.
6. **Digest** — new eligible jobs go to Telegram; everything is browsable on a local htmx page.
7. **Tailored resume** — for strong matches, a resume rewritten for the job from the candidate's base resume, never adding facts.

Applying stays human-in-the-loop: ATS submit APIs only accept employer credentials and their forms carry CAPTCHAs ([why](docs/adr/0002-assisted-apply-no-ats-submit-api.md)).

## Docs

- [Product](docs/product.md)
- [Architecture](docs/architecture.md)
- [Domain glossary](CONTEXT.md)
- [Architecture decision records](docs/adr/)

## Running it

Setup instructions arrive with the first implementation. Personal data lives in a gitignored `private/` folder; `private.example/` will provide a fictional candidate to run the whole flow.

## License

[MIT](LICENSE)
