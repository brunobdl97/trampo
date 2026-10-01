# Trampo

A personal job-search agent that finds remote engineering jobs a candidate living in Brazil can actually take, judges each one against the candidate's profile, and prepares a tailored resume for the best matches.

## Language

### Sourcing

**ATS**:
An applicant tracking system that hosts companies' public job boards (Ashby, Greenhouse, Lever).
_Avoid_: job site, platform

**Board**:
A company's public list of postings on one ATS, identified by ATS + slug.
_Avoid_: careers page, job board (alone)

**Discovery**:
Finding new Boards worth tracking by searching the web.
_Avoid_: crawling, sourcing

**Posting**:
One entry on a Board, identified by ATS + posting ID, for a single location.
_Avoid_: listing, opening, position

**Job**:
A deduplicated opportunity (same company and normalized title) grouping one or more Postings. Verdict, Status, notes and Tailored resume belong to the Job.
_Avoid_: vaga, opening, position, role

**Repost**:
A new Posting for a Job already seen in the last 30 days; it joins that Job instead of creating a new one.

### Evaluation

**Track**:
The career line a Job is judged against: Backend or Agents/Automation.
_Avoid_: category, profile

**Fit score**:
A 0–10 rating of how well the Candidate matches a Job within its Track.
_Avoid_: match, adherence, score (alone)

**Verdict**:
The agent's eligibility decision on a Job — `pending`, `eligible`, `needs_review` or `rejected` — always with a reason.
_Avoid_: result, classification, "discarded"

**Status**:
The Candidate's own progress on a Job — `new`, `seen`, `applied` or `dismissed`. A Run also sets `dismissed` on untouched (`new`) Jobs that are `rejected` or at or below the Fit-score threshold.
_Avoid_: state, "discarded"

**Override**:
The Candidate replacing the agent's Verdict on a Job; kept as a labeled example for measuring the agent.
_Avoid_: correction, feedback

### Candidate

**Candidate**:
The person the agent searches on behalf of.
_Avoid_: user, applicant

**Base resume**:
The Candidate's single source-of-truth resume.
_Avoid_: CV, master resume

**Tailored resume**:
A version of the Base resume rewritten for one Job, never containing facts absent from the Base resume.
_Avoid_: custom CV, adapted resume

**Salary floor**:
The minimum monthly pay the Candidate accepts, per contract type (CLT, PJ, international contractor).

### Operation

**Run**:
One execution of the daily cycle: Discovery, collecting Postings, judging Jobs, and sending the Digest.
_Avoid_: job (ambiguous), execution, sync

**Digest**:
The daily message to the Candidate listing a Run's new eligible Jobs.
_Avoid_: notification, report, summary

**Assisted apply**:
Filling a Job's application form on the Candidate's behalf, leaving review and submission to the Candidate.
_Avoid_: auto-apply
