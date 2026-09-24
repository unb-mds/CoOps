"""Console entry point ``coops-corpus`` — build and inspect the corpus artifacts.

``sanitize`` turns a private ``corpus-raw`` tree into the shareable
``corpus-fixtures`` tree. Packing/unpacking and the two-artifact split live in
``scripts/data-snapshot.sh``, which calls this command for the sanitize step.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from coops.raw_capture.capture import prune_directory
from coops.raw_capture.sanitize import sanitize_directory


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coops-corpus",
        description="Build and inspect the CoOps raw API corpus artifacts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sanitize = sub.add_parser(
        "sanitize",
        help="produce shareable corpus-fixtures from a private corpus-raw tree",
    )
    sanitize.add_argument(
        "--raw", required=True, help="source corpus-raw directory (private)"
    )
    sanitize.add_argument(
        "--out", required=True, help="destination corpus-fixtures directory"
    )

    prune = sub.add_parser(
        "prune",
        help="delete corpus-raw records older than --max-age-days (retention)",
    )
    prune.add_argument("--raw", required=True, help="corpus-raw directory (private)")
    prune.add_argument(
        "--max-age-days",
        type=int,
        required=True,
        help="retention window in days; records older than this are deleted",
    )

    args = parser.parse_args(argv)

    if args.command == "sanitize":
        count = sanitize_directory(args.raw, args.out)
        print(f"sanitized {count} record(s) -> {args.out}")
        return 0

    if args.command == "prune":
        if args.max_age_days < 1:
            parser.error("--max-age-days must be a positive integer")
        removed = prune_directory(args.raw, args.max_age_days)
        print(f"pruned {removed} stale record(s) from {args.raw}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
