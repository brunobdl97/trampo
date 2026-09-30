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
  job_ids TEXT NOT NULL, model_id TEXT NOT NULL, prompt_hash TEXT NOT NULL,  -- what it was submitted with
  submitted_at TEXT NOT NULL, collected_at TEXT);
CREATE TABLE discovery_searches (id INTEGER PRIMARY KEY, searched_on TEXT NOT NULL, query TEXT NOT NULL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
PRAGMA user_version = 1;
