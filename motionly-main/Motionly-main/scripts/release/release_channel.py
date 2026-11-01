#!/usr/bin/env python3
"""Classify a Git ref as a staging or production release channel.

Only an exact staging release tag, ``refs/tags/vMAJOR.MINOR.PATCH-staging.N``,
is the staging channel. Every other ref (production tags such as ``v1.2.3``,
any other ``v*`` tag, branches, pull requests, or an empty ref) is the
production channel, which keeps every release gate blocking. The match is
exact so a near miss (``v1.2.3-staging``, ``v1.2.3-staging.1-rc``, a branch
named like a staging tag) can never pick up the staging exception.

Staging differs in one way only: the browser end-to-end suite and the k6 load
smoke (the ``cpu-stage-browser`` readiness job) still run and report, but their
failure does not block the staging release. Staging releases deploy only to the
GitHub ``staging`` environment, never ``production``.

Usage::

    python scripts/release/release_channel.py --ref "$GITHUB_REF" [--github-output FILE]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

_NUMBER: Final = r"(0|[1-9][0-9]*)"
STAGING_TAG: Final = re.compile(rf"refs/tags/v{_NUMBER}\.{_NUMBER}\.{_NUMBER}-staging\.[1-9][0-9]*")
STAGING: Final = "staging"
PRODUCTION: Final = "production"


def channel(ref: str) -> str:
    """``staging`` for an exact staging release tag ref, otherwise ``production``."""
    return STAGING if STAGING_TAG.fullmatch(ref or "") else PRODUCTION


def outputs(ref: str) -> dict[str, str]:
    """The values workflows read: the channel, whether the browser and k6 job is
    advisory (staging only), and the GitHub environment a release deploys to."""
    name = channel(ref)
    return {
        "channel": name,
        "browser_perf_advisory": "true" if name == STAGING else "false",
        "deploy_environment": STAGING if name == STAGING else PRODUCTION,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", required=True, help="full Git ref, e.g. refs/tags/v1.2.3")
    parser.add_argument("--github-output", type=Path, help="append key=value lines here")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    values = outputs(args.ref)
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(lines)
    sys.stdout.write(f"release channel for {args.ref or '<empty>'}:\n{lines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
