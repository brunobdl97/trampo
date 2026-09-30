"""Argparse entry point for the `trampo` command; each task registers its own subcommand."""

import argparse
import sys
from importlib.metadata import version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trampo")
    parser.add_argument("--version", action="version", version=f"trampo {version('trampo')}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
