TRAMPO := uv run --env-file private/.env trampo
PORT ?= 8765
RESUME_LANG ?= en

.PHONY: help install run serve resume base eval test types lint fmt check

help:  ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

install:  ## Install deps and the Chromium used for PDFs
	uv sync
	uv run playwright install --with-deps chromium

run:  ## One Run: collect, judge, send the Digest
	$(TRAMPO) run

serve:  ## Local page on http://localhost:8765 (PORT=...)
	$(TRAMPO) serve --port $(PORT)

resume:  ## Tailored resume for one Job (JOB=<id>)
	@test -n "$(JOB)" || { echo "usage: make resume JOB=<job_id>"; exit 1; }
	$(TRAMPO) resume $(JOB)

base:  ## Render the Base resume (RESUME_LANG=en|pt)
	uv run trampo resume --base --lang $(RESUME_LANG)

eval:  ## Re-judge overridden Jobs (costs money; asks first)
	$(TRAMPO) eval

test:  ## pytest
	uv run pytest

types:  ## pyright
	uv run pyright

lint:  ## ruff check + format check
	uv run ruff check . && uv run ruff format --check .

fmt:  ## ruff fix + format
	uv run ruff check --fix . && uv run ruff format .

check: types test lint  ## Everything a task needs to be done
