"""Fail closed on unreadable refs and skip obsolete queued test releases."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("RELEASE_SOURCE_READ_FAILED")
    return result.stdout.strip()


def current_source(branch: str, revision: str) -> bool:
    if branch not in ("develop", "main") or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("INVALID_RELEASE_SOURCE")
    if git("rev-parse", "HEAD") != revision:
        raise ValueError("RELEASE_CHECKOUT_REVISION_MISMATCH")
    ref = f"refs/heads/{branch}"
    rows = git("ls-remote", "--exit-code", "origin", ref).splitlines()
    if len(rows) != 1:
        raise ValueError("RELEASE_BRANCH_REF_AMBIGUOUS")
    fields = rows[0].split()
    if len(fields) != 2 or fields[1] != ref or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
        raise ValueError("INVALID_RELEASE_BRANCH_REF")
    return fields[0] == revision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    current = current_source(args.branch, args.revision)
    value = "true" if current else "false"
    if not current:
        print("Queued source no longer matches the branch head; skipping release.", file=sys.stderr)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"current={value}\nrevision={args.revision}\n")
    print(value)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        # Avoid printing remote URLs, command output or credentials on failures.
        reason = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[A-Z_]{1,100}", str(error)) else type(error).__name__
        raise SystemExit("RELEASE_SOURCE_CHECK_FAILED: " + reason) from None
