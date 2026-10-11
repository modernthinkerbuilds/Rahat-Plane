#!/usr/bin/env python3
"""check_private_terms — a push never publishes a private word.

The repo is public. The words that must stay out of it live only in
gitignored places, never in the repo itself:

  private/privacy_terms.txt  One word or phrase per line: a name, a surname,
                             a street, an employer, a phone fragment. "#"
                             starts a comment. A word that is public by
                             design in some pages (a byline) says where:
                                 Jane Roe | allow README.md profile/*
  .env                       Every email address in it, except your own
                             commit address (already on every commit) and
                             placeholders such as example.com; every
                             *CHAT_ID number; every token, key, secret or
                             password value.

A push publishes each commit it carries: the lines it adds, the paths it
adds and its message. A word added in one commit and removed in the next
still goes public, so each commit is checked on its own. Strings split to
slip past a scan ("Ja" + "ne") are joined first. A word of six or more
characters matches inside a longer word too (a name inside a mailbox
name); a shorter one only as a whole word. A digit run ("555 0199")
matches with any separator.

A hit blocks the push and names the commit and file:line, with the word
masked: this check never prints a private word or an .env value.

No list and no .env (CI, a fresh clone): a notice, and a pass.

  scripts/check_private_terms.py --pre-push origin --refs-file F   the hook
  scripts/check_private_terms.py --range main..HEAD                these commits
  scripts/check_private_terms.py --tree [REV]                      what is public now

A deliberate exception: RAHAT_PRIVACY_OK=1 git push …  (lists, then passes)
"""
# No `from __future__ import annotations`: string annotations make a dataclass
# need this module in sys.modules, and the pins load it straight from its path.
import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

TERMS_FILE = "private/privacy_terms.txt"
ENV_FILE = ".env"
ANYWHERE_MIN = 6            # a word this long also matches inside a longer word
NUMBER_MIN_DIGITS = 6       # a digit run this long is a number term (phone, chat id)
PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net", "localhost")
PLACEHOLDER_SUFFIXES = (".invalid", ".test", ".example", ".localhost", ".local")

_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_SECRET_KEY = re.compile(r"TOKEN|SECRET|PASSW|API_?KEY|(?:^|_)KEY$", re.I)
_CHAT_KEY = re.compile(r"CHAT_?IDS?$", re.I)
_SPLIT = re.compile(r"""["']\s*\+?\s*["']""")      # "Ja" + "ne", "Ja" "ne"
_NUMERIC = re.compile(r"^[\d\s().+-]+$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_HEADER = re.compile(r"^diff --git a/(.*) b/(.*)$")
_ZERO = re.compile(r"^0+$")


def _compile(word: str, kind: str) -> "re.Pattern[str]":
    if kind == "number":
        groups = re.findall(r"\d+", word)
        tail = r"(?!\d)" if len(groups) == 1 else ""
        return re.compile(r"(?<!\d)" + r"[\s().+-]*".join(groups) + tail)
    body = r"\s+".join(re.escape(part) for part in word.split())
    if kind != "word" or len(word.replace(" ", "")) >= ANYWHERE_MIN:
        return re.compile(body, re.I)
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", re.I)


@dataclass(frozen=True)
class Term:
    word: str
    source: str                          # "private list" or ".env <KEY>"
    kind: str = "word"                   # word | number | address | secret
    allow: Tuple[str, ...] = ()
    regex: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "regex", _compile(self.word, self.kind))

    def allowed_in(self, path: Optional[str]) -> bool:
        return bool(path) and any(fnmatch.fnmatch(path, g) for g in self.allow)

    @property
    def masked(self) -> str:
        if self.kind == "secret":
            return f"<{self.source}>"
        if self.kind == "address":
            local, _, domain = self.word.partition("@")
            return f"{local[:1]}***@{domain[:1]}***"
        return self.word[:1] + "*" * (len(self.word) - 1)

    @property
    def label(self) -> str:
        return self.masked if self.kind == "secret" else f"{self.masked} ({self.source})"


def mask(text: str, terms: Iterable[Term]) -> str:
    """The text with every private word in it masked (paths can carry one)."""
    for t in terms:
        text = t.regex.sub(lambda _m, t=t: t.masked, text)
    return text


@dataclass(frozen=True)
class Finding:
    commit: Optional[str]
    path: Optional[str]                  # None: the commit message
    line: Optional[int]                  # 0: the path itself; None: split across lines
    term: Term

    def where(self, terms: Iterable[Term]) -> str:
        if self.path is None:
            return "commit message"
        p = mask(self.path, terms)
        if self.line == 0:
            return f"{p} (the path itself)"
        if self.line is None:
            return f"{p} (split across lines)"
        return f"{p}:{self.line}"


# ── where the words come from ───────────────────────────────────────────
def is_placeholder(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return domain in PLACEHOLDER_DOMAINS or domain.endswith(PLACEHOLDER_SUFFIXES)


def _list_terms(path: Path) -> List[Term]:
    terms: List[Term] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        word, _, rule = line.partition("|")
        word = " ".join(word.split())
        if len(word) < 2:
            continue
        head, _, globs = rule.strip().partition(" ")
        allow = tuple(globs.split()) if head.lower().rstrip(":") == "allow" else ()
        digits = sum(c.isdigit() for c in word)
        kind = "number" if _NUMERIC.match(word) and digits >= NUMBER_MIN_DIGITS else "word"
        terms.append(Term(word, "private list", kind, allow))
    return terms


def _env_values(path: Path):
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if value:
            yield key, value


def _env_terms(path: Path, own_address: str) -> List[Term]:
    terms: List[Term] = []
    seen = set()

    def add(word: str, key: str, kind: str) -> None:
        if (kind, word.lower()) not in seen:
            seen.add((kind, word.lower()))
            terms.append(Term(word, f".env {key}", kind))

    for key, value in _env_values(path):
        for address in _ADDRESS.findall(value):
            if address.lower() != own_address and not is_placeholder(address):
                add(address.lower(), key, "address")
        if _CHAT_KEY.search(key):
            for number in re.findall(r"\d{%d,}" % NUMBER_MIN_DIGITS, value):
                add(number, key, "number")
        elif (_SECRET_KEY.search(key) and len(value) >= 8 and not value.isdigit()
              and not _ADDRESS.search(value) and not value.startswith(("/", "~", "."))):
            add(value, key, "secret")
    return terms


def load_terms(root: Path) -> List[Term]:
    """Every private word: the gitignored list plus what .env holds."""
    terms: List[Term] = []
    listed = root / TERMS_FILE
    if listed.is_file():
        terms += _list_terms(listed)
    env = root / ENV_FILE
    if env.is_file():
        own = _git_quiet(root, "config", "user.email").strip().lower()
        terms += _env_terms(env, own)
    return terms


# ── matching ────────────────────────────────────────────────────────────
def _hits(text: str, terms: Iterable[Term], path: Optional[str]) -> List[Term]:
    joined = _SPLIT.sub("", text)
    return [t for t in terms if not t.allowed_in(path)
            and (t.regex.search(text) or (joined != text and t.regex.search(joined)))]


def find_in_rows(rows: Iterable[Tuple[int, str]], terms: List[Term], path: Optional[str],
                 commit: Optional[str] = None) -> List[Finding]:
    return [Finding(commit, path, n, t) for n, text in rows for t in _hits(text, terms, path)]


def find_in_text(text: str, terms: List[Term], path: str,
                 commit: Optional[str] = None) -> List[Finding]:
    """Every hit in a file's text, by line."""
    live = _hits(text, terms, path)
    if not live:
        return []
    found = find_in_rows(enumerate(text.split("\n"), 1), live, path, commit)
    return found or [Finding(commit, path, None, live[0])]


# ── git ─────────────────────────────────────────────────────────────────
def _git(root: Path, *args: str, stdin: Optional[str] = None) -> str:
    p = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=str(root),
                       input=stdin.encode() if stdin is not None else None,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"git {args[0]} failed: {err[0] if err else p.returncode}")
    return p.stdout.decode("utf-8", "replace")


def _git_quiet(root: Path, *args: str) -> str:
    try:
        return _git(root, *args)
    except RuntimeError:
        return ""


def pushed_commits(root: Path, remote: str, refs: str) -> List[str]:
    """The commits a push publishes: reachable from what is pushed and on no
    branch of the remote yet. With no refs (run by hand), what HEAD would add."""
    rows = [line.split() for line in refs.splitlines() if line.strip()]
    if not rows:
        rows = [["HEAD", _git(root, "rev-parse", "HEAD").strip(), "-", "0"]]
    shas: List[str] = []
    for row in rows:
        if len(row) != 4 or _ZERO.match(row[1]):        # a deleted branch publishes nothing
            continue
        args = ["rev-list", "--reverse", "--topo-order", row[1], "--not", f"--remotes={remote}"]
        if not _ZERO.match(row[3]) and _git_quiet(root, "cat-file", "-t", row[3]).strip() == "commit":
            args.append(row[3])
        shas += _git(root, *args).split()
    return list(dict.fromkeys(shas))


def _added(patch: str) -> List[Tuple[str, List[Tuple[int, str]]]]:
    """(path, rows) per file of a -U0 patch: the lines it adds, and the path
    itself as row 0 when the path is new (an added, renamed or copied file)."""
    files: List[Tuple[str, List[Tuple[int, str]]]] = []
    cur: Optional[dict] = None

    def close() -> None:
        if cur and cur["path"] and (cur["rows"] or cur["new"]):
            files.append((cur["path"], ([(0, cur["path"])] if cur["new"] else []) + cur["rows"]))

    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            close()
            m = _HEADER.match(line)
            cur = {"path": m.group(2) if m else None, "new": False, "hunk": False, "n": 0, "rows": []}
        elif cur is None:
            continue
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            cur["n"], cur["hunk"] = (int(m.group(1)) if m else 0), True
        elif cur["hunk"]:
            if line.startswith("+"):
                cur["rows"].append((cur["n"], line[1:]))
                cur["n"] += 1
            elif line.startswith(" "):
                cur["n"] += 1
        elif line.startswith(("new file mode", "rename to ", "copy to ")):
            cur["new"] = True
            if " to " in line and not line.startswith("new file"):
                cur["path"] = line.split(" to ", 1)[1]
        elif line.startswith("+++ "):
            target = line[4:].rstrip("\t")
            if target != "/dev/null":
                cur["path"] = target[2:] if target.startswith("b/") else target
    close()
    return files


def scan_commits(root: Path, shas: List[str], terms: List[Term]) -> List[Finding]:
    if not shas:
        return []
    out = _git(root, "log", "--no-walk=unsorted", "--stdin", "-p", "-U0", "--no-color",
               "--no-ext-diff", "--no-textconv", "-M", "--diff-merges=first-parent",
               "--src-prefix=a/", "--dst-prefix=b/", "--format=%x00%H%x00%B%x00",
               stdin="\n".join(shas) + "\n")
    parts = out.split("\x00")
    found: List[Finding] = []
    for i in range(1, len(parts) - 1, 3):
        short, message = parts[i].strip()[:9], parts[i + 1]
        patch = parts[i + 2] if i + 2 < len(parts) else ""
        found += [Finding(short, None, None, t) for t in _hits(message, terms, None)]
        for path, rows in _added(patch):
            found += find_in_rows(rows, terms, path, short)
    return found


def scan_tree(root: Path, rev: str, terms: List[Term]) -> List[Finding]:
    entries = []
    for rec in _git(root, "ls-tree", "-r", "-z", "--full-tree", rev).split("\x00"):
        if "\t" in rec:
            meta, path = rec.split("\t", 1)
            mode, kind, sha = meta.split()
            if kind == "blob" and mode in ("100644", "100755"):
                entries.append((sha, path))
    found: List[Finding] = []
    for _, path in entries:
        found += find_in_rows([(0, path)], terms, path)
    if not entries:
        return found
    p = subprocess.run(["git", "cat-file", "--batch"], cwd=str(root),
                       input=("\n".join(s for s, _ in entries) + "\n").encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("git cat-file failed")
    data, pos = p.stdout, 0
    for _, path in entries:
        eol = data.index(b"\n", pos)
        size = int(data[pos:eol].split()[2])
        body = data[eol + 1:eol + 1 + size]
        pos = eol + 1 + size + 1
        if b"\x00" not in body[:8000]:
            found += find_in_text(body.decode("utf-8", "replace"), terms, path)
    return found


# ── the report ──────────────────────────────────────────────────────────
def report(findings: List[Finding], terms: List[Term], what: str, override: bool,
           audit: bool) -> int:
    hits = list(dict.fromkeys(findings))
    if not hits:
        print(f"private words: {len(terms)} words checked across {what} — clean")
        return 0
    commits = len({f.commit for f in hits if f.commit})
    print(f"private words already public in {what}: {len(hits)}" if audit else
          f"private words: {len(hits)} hit{'' if len(hits) == 1 else 's'} in {commits} of {what}")
    for f in hits:
        print(f"  {f.where(terms)}  {f.term.label}" if audit else
              f"  {f.commit}  {f.where(terms)}  {f.term.label}")
    if audit:
        print("These are public already: fix forward with a commit that takes them out.")
        return 1
    if override:
        print("RAHAT_PRIVACY_OK=1 — passing anyway, by your decision.")
        return 0
    print("These commits are not public yet, so take the word out of the commit that adds it")
    print("(git commit --amend for the last one, a rebase for an older one): a later commit")
    print("that removes it is not enough, the earlier one still goes public.")
    print(f"A word public by design in some pages: add '| allow <glob>' to its line in {TERMS_FILE}.")
    print("A deliberate one-off: RAHAT_PRIVACY_OK=1 git push …")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="A push never publishes a private word.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pre-push", metavar="REMOTE", help="the hook: what this push publishes")
    mode.add_argument("--range", metavar="BASE..HEAD", help="these commits")
    mode.add_argument("--tree", nargs="?", const="HEAD", metavar="REV", help="a whole tree")
    ap.add_argument("--refs-file", help="the lines git handed the pre-push hook on stdin")
    args = ap.parse_args(argv)
    override = os.environ.get("RAHAT_PRIVACY_OK") == "1"
    try:
        root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        terms = load_terms(root)
        if not terms:
            print(f"private words: no {TERMS_FILE} and no {ENV_FILE} here — nothing to check")
            return 0
        if args.tree is not None:
            return report(scan_tree(root, args.tree, terms), terms, f"the tree at {args.tree}",
                          override, audit=True)
        if args.pre_push is not None:
            refs = Path(args.refs_file).read_text() if args.refs_file else ""
            shas = pushed_commits(root, args.pre_push, refs)
        else:
            shas = _git(root, "rev-list", "--reverse", "--topo-order", args.range).split()
        what = f"{len(shas)} commit{'' if len(shas) == 1 else 's'}"
        return report(scan_commits(root, shas, terms), terms, what, override, audit=False)
    except Exception as exc:                 # the check itself failed: say so, never pass quietly
        print(f"private words: the check could not run ({type(exc).__name__}: {exc})")
        if override:
            print("RAHAT_PRIVACY_OK=1 — passing anyway, by your decision.")
            return 0
        print("Look at the commits yourself, then push with RAHAT_PRIVACY_OK=1.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
