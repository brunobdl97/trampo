"""Argparse entry point for the `trampo` command; each task registers its own subcommand."""

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import httpx2
import uvicorn

from trampo.ats import client_for
from trampo.claude import SpendLimitReached, load_prompt, make_client, newest_opus
from trampo.config import Paths, load_config, load_profile, private_paths
from trampo.digest import Telegram, format_failure
from trampo.evaluate import EvalReport, evaluate
from trampo.pipeline import RunContext, run, send_alert
from trampo.resume import load_resume
from trampo.resume.render import html_to_pdf, render_html
from trampo.resume.tailor import InventedFactsError, TailorRefused, tailor_now
from trampo.store import JobNotFound, Store
from trampo.web import MissingApiKey, create_app

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trampo")
    parser.add_argument("--version", action="version", version=f"trampo {version('trampo')}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="one daily Run: collect, judge, send the Digest")
    resume_parser = commands.add_parser("resume", help="render a resume to PDF")
    resume_parser.add_argument(
        "job_id", nargs="?", type=int, help="write the Tailored resume for this Job"
    )
    resume_parser.add_argument("--base", action="store_true", help="render the Base resume")
    resume_parser.add_argument("--lang", choices=["en", "pt"], default="en")
    serve_parser = commands.add_parser("serve", help="local Job page (UI in pt-BR), 127.0.0.1 only")
    serve_parser.add_argument("--port", type=int, default=8765)
    eval_parser = commands.add_parser(
        "eval", help="re-judge every overridden Job, compare to the Candidate's Overrides"
    )
    eval_parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    return parser


def _setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{datetime.now(UTC):%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    # httpx2 logs every request URL at INFO, and Telegram's contains the bot token.
    logging.getLogger("httpx2").setLevel(logging.WARNING)


_CREDENTIALS = ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def _http() -> httpx2.Client:
    """The HTTP client shared by the ATS clients and Telegram (a test seam)."""
    return httpx2.Client(timeout=30.0)


def _context(paths: Paths, http: httpx2.Client, telegram: Telegram) -> RunContext:
    config = load_config()
    profile = load_profile(paths.profile_toml)
    claude = make_client()
    return RunContext(
        store=Store(paths.db),
        config=config,
        profile=profile,
        paths=paths,
        claude=claude,
        ats_clients={ats: client_for(ats, http) for ats in ("greenhouse", "lever", "ashby")},
        telegram=telegram,
        now=lambda: datetime.now(UTC),
        sleep=time.sleep,
    )


def _run() -> int:
    # Fail fast, before building anything: names only, never a value.
    missing = [name for name in _CREDENTIALS if not os.environ.get(name)]
    if missing:
        print(f"trampo run: missing {', '.join(missing)} (private/.env)", file=sys.stderr)
        return 2

    paths = private_paths()
    _setup_logging(paths.logs_dir)
    with _http() as http:
        telegram = Telegram(http, os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
        try:
            ctx = _context(paths, http, telegram)
        except Exception as exc:  # config typo, locked DB...: never fail silently
            logger.exception("Could not set up the Run")
            send_alert(telegram, format_failure(f"{type(exc).__name__}: {exc}"))
            return 1
        try:
            run(ctx)
        except Exception:
            logger.exception("Run failed")  # run() already recorded it and alerted
            return 1
        finally:
            ctx.store.close()
    return 0


def _resume(args: argparse.Namespace) -> int:
    if args.base == (args.job_id is not None):
        print("trampo resume: give either a Job id or --base", file=sys.stderr)
        return 2
    if args.job_id is not None:
        return _tailor(args.job_id)

    paths = private_paths()
    resume = load_resume(paths.resume_json)
    html = render_html(resume, args.lang)
    out = paths.resumes_dir / f"base-{args.lang}.pdf"
    html_to_pdf(html, out)
    print(out)
    return 0


def _tailor(job_id: int) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("trampo resume: missing ANTHROPIC_API_KEY (private/.env)", file=sys.stderr)
        return 2

    paths = private_paths()
    with _http() as http:
        # Telegram is part of a RunContext but never used here: no credentials needed.
        ctx = _context(paths, http, Telegram(http, "", ""))
        try:
            print(tailor_now(ctx, job_id))
        except InventedFactsError as exc:
            facts = "\n".join(f"- {fact}" for fact in exc.facts)
            print(
                f"trampo resume: refused, facts absent from the Base resume:\n{facts}",
                file=sys.stderr,
            )
            return 1
        except SpendLimitReached:
            print("trampo resume: Anthropic spend limit reached", file=sys.stderr)
            return 1
        except (JobNotFound, TailorRefused) as exc:
            print(f"trampo resume: {exc}", file=sys.stderr)
            return 1
        finally:
            ctx.store.close()
    return 0


def _serve_tailor(ctx: RunContext) -> Callable[[int], Path]:
    """The `tailor` callable `trampo serve` passes to create_app: tailor_now(ctx,
    job_id), but a missing ANTHROPIC_API_KEY raises a friendly error instead of
    the SDK's — `trampo serve` itself starts fine without any env var, this is
    only checked when the Candidate clicks "Gerar currículo"."""

    def tailor(job_id: int) -> Path:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise MissingApiKey("ANTHROPIC_API_KEY não configurada (private/.env)")
        return tailor_now(ctx, job_id)

    return tailor


def _serve(args: argparse.Namespace) -> int:
    paths = private_paths()
    with _http() as http:
        # Telegram is part of a RunContext but never used here: the page never sends messages.
        ctx = _context(paths, http, Telegram(http, "", ""))
        try:
            app = create_app(ctx.store, paths, _serve_tailor(ctx), lambda: datetime.now(UTC))
            uvicorn.run(app, host="127.0.0.1", port=args.port)
        finally:
            ctx.store.close()
    return 0


def _print_eval_report(report: EvalReport, model: str, prompt_hash: str) -> None:
    pct = 100 * report.agree / report.total if report.total else 0.0
    print(f"Agreement: {report.agree}/{report.total} ({pct:.0f}%)")
    print("Confusion (override verdict -> judge verdict): count")
    for (expected, got), count in sorted(report.confusion.items()):
        print(f"  {expected} -> {got}: {count}")
    print("Disagreements:")
    for job_id, expected, got in report.disagreements:
        print(f"  Job {job_id}: expected {expected}, got {got}")
    print(f"Model: {model}")
    print(f"Prompt hash: {prompt_hash}")


def _eval(args: argparse.Namespace) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("trampo eval: missing ANTHROPIC_API_KEY (private/.env)", file=sys.stderr)
        return 2

    paths = private_paths()
    store = Store(paths.db)
    try:
        overrides = store.overridden_jobs()
        if not overrides:
            print("trampo eval: no Overrides to evaluate")
            return 0

        print(f"trampo eval: {len(overrides)} API calls will be made (this costs money).")
        if not args.yes:
            try:
                answer = input("Continue? [y/N] ")
            except EOFError:  # stdin closed/exhausted (cron, CI, `< /dev/null`): decline
                print("\nAborted.")
                return 0
            if answer.strip().lower() not in ("y", "yes"):
                return 0

        config = load_config()
        profile = load_profile(paths.profile_toml)
        resume = load_resume(paths.resume_json)
        claude = make_client()
        model = newest_opus(claude)
        prompt_hash = load_prompt("judge").hash

        report = evaluate(claude, store, config, profile, resume, model)
        _print_eval_report(report, model, prompt_hash)
    except SpendLimitReached:
        print("trampo eval: Anthropic spend limit reached", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run()
    if args.command == "resume":
        return _resume(args)
    if args.command == "serve":
        return _serve(args)
    if args.command == "eval":
        return _eval(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
