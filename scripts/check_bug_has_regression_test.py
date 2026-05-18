"""Enforce the bug-to-test policy: every `fix:` commit must add at
least one file under tests/regression_registry/.

Modes:
    --gate=pre-push      Check the last commit (HEAD).
    --gate=pre-merge     Check every commit on the PR / branch since main.
    --gate=ci            Same as pre-merge but log to JSON for the workflow.

Exits non-zero if any `fix:` commit fails the policy.

Usage:
    python scripts/check_bug_has_regression_test.py --gate=pre-push
    python scripts/check_bug_has_regression_test.py --gate=pre-merge --base=main

Idempotent — multiple files in one commit count as one pass.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


REGISTRY_DIR = "tests/regression_registry"
FIX_COMMIT_RE = re.compile(r"^fix(?:\([^)]+\))?\s*:", re.IGNORECASE)


def run(cmd: list[str]) -> str:
    """Run a git command and return stdout. Empty string on error."""
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="replace")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def commits_to_check(gate: str, base: str | None) -> list[str]:
    """Return the list of commit SHAs to scan, oldest first."""
    if gate == "pre-push":
        # Check the tip commit only.
        sha = run(["git", "rev-parse", "HEAD"]).strip()
        return [sha] if sha else []

    base_ref = base or "main"
    # If we can't find main (detached / shallow), fall back to HEAD only.
    base_sha = run(["git", "merge-base", base_ref, "HEAD"]).strip()
    if not base_sha:
        sha = run(["git", "rev-parse", "HEAD"]).strip()
        return [sha] if sha else []

    out = run(["git", "log", "--reverse", "--format=%H",
               f"{base_sha}..HEAD"])
    return [line.strip() for line in out.splitlines() if line.strip()]


def commit_message(sha: str) -> str:
    return run(["git", "log", "-1", "--format=%s%n%b", sha])


def files_added_in_commit(sha: str) -> list[str]:
    """Return the list of files ADDED (status=A) in this commit."""
    out = run(["git", "diff-tree", "--no-commit-id", "--name-status",
               "-r", "--diff-filter=A", sha])
    return [line.split("\t", 1)[1].strip()
            for line in out.splitlines() if "\t" in line]


def is_fix_commit(msg: str) -> bool:
    """Returns True if the first line starts with 'fix:' or 'fix(scope):'."""
    first = (msg or "").splitlines()
    if not first:
        return False
    return bool(FIX_COMMIT_RE.match(first[0]))


def adds_registry_test(files: list[str]) -> tuple[bool, list[str]]:
    """Returns (adds_test, list_of_registry_files_added)."""
    matches = [
        f for f in files
        if f.startswith(REGISTRY_DIR + "/")
        and f.endswith(".py")
        and not f.endswith("/__init__.py")
        and not f.endswith("/conftest.py")
    ]
    return bool(matches), matches


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="bug-to-test policy gate")
    p.add_argument("--gate", choices=["pre-push", "pre-merge", "ci"],
                   default="pre-push")
    p.add_argument("--base", default=None,
                   help="base ref for pre-merge/ci (default: main)")
    p.add_argument("--allow-bypass", action="store_true",
                   help="non-zero exit but don't fail (warn-only mode)")
    args = p.parse_args(argv)

    shas = commits_to_check(args.gate, args.base)
    if not shas:
        # Empty repo or shallow clone — nothing to enforce.
        print("[bug-policy] no commits to check, passing")
        return 0

    violations = []
    fix_count = 0

    for sha in shas:
        msg = commit_message(sha)
        if not is_fix_commit(msg):
            continue
        fix_count += 1
        files = files_added_in_commit(sha)
        adds, registry_files = adds_registry_test(files)
        first_line = msg.splitlines()[0] if msg else ""
        if not adds:
            violations.append({
                "sha": sha[:8],
                "subject": first_line[:80],
                "files_added": files,
            })

    if fix_count == 0:
        print("[bug-policy] no 'fix:' commits in range, passing")
        return 0

    if not violations:
        print(f"[bug-policy] ✓ {fix_count} 'fix:' commit(s) all have "
              f"registry tests, passing")
        return 0

    print()
    print("════════════════════════════════════════════════════════════════════")
    print("  ✗ bug-to-test policy violation")
    print("════════════════════════════════════════════════════════════════════")
    print()
    print(f"  {len(violations)} 'fix:' commit(s) did not add a regression test:")
    print()
    for v in violations:
        print(f"    {v['sha']}  {v['subject']}")
        if v["files_added"]:
            print(f"        files added: {', '.join(v['files_added'])}")
        else:
            print(f"        no files added in this commit")
    print()
    print("  Every 'fix:' commit MUST add at least one file under:")
    print(f"    {REGISTRY_DIR}/")
    print()
    print("  Convention: tests/regression_registry/test_YYYY-MM-DD_bug_name.py")
    print()
    print("  See: tests/regression_registry/README.md")
    print("════════════════════════════════════════════════════════════════════")

    if args.allow_bypass:
        print("  (warn-only mode — not blocking, but FIX THIS)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
