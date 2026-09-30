"""Argparse entry point for the `trampo` command; each task registers its own subcommand."""

import argparse
import logging
import os
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import httpx2

from trampo.ats import client_for
from trampo.claude import make_client
from trampo.config import Paths, load_config, load_profile, private_paths
from trampo.digest import Telegram, format_failure
from trampo.pipeline import RunContext, run, send_alert
from trampo.resume import load_resume
from trampo.resume.render import html_to_pdf, render_html
from trampo.store import Store

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trampo")
    parser.add_argument("--version", action="version", version=f"trampo {version('trampo')}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="one daily Run: collect, judge, send the Digest")
    resume_parser = commands.add_parser("resume", help="render a resume to PDF")
    resume_parser.add_argument("--base", action="store_true", help="render the Base resume")
    resume_parser.add_argument("--lang", choices=["en", "pt"], default="en")
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
    if not args.base:
        print("trampo resume: use --base (tailored resumes: Task 16)", file=sys.stderr)
        return 2

    paths = private_paths()
    resume = load_resume(paths.resume_json)
    html = render_html(resume, args.lang)
    out = paths.resumes_dir / f"base-{args.lang}.pdf"
    html_to_pdf(html, out)
    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run()
    if args.command == "resume":
        return _resume(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
