"""Regression (2026-10-11): a push never publishes a private word.

LIVE: a regression pin (commit 9e16819) quoted Miya's live reply, which
opened with the owner's first name, and went out in a push the gate
passed; a person caught it after the push. An older pin (2026-08-17) held
the co-owner's identity spelled in halves ("ab" + "cd") so that its own
scan would not trip on it, which anyone reading the public file can join.

Fix: scripts/check_private_terms.py is the first step of the pre-push
gate. Its words come only from gitignored places (private/privacy_terms.txt
and .env). It checks every commit the push publishes (the lines it adds,
the paths it adds, its message), joins split strings before matching, and
blocks on a hit with the word masked. The leaking pins were scrubbed in
the same change.

Everything below runs in a throwaway repository with invented words.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_private_terms.py"
HOOK = ROOT / "scripts" / "hooks" / "pre-push"

TERMS = """# invented words
Zebulon | allow README.md docs/*
Quillfeather
Ash
555 0199
"""
ENV = """BENJI_DELIVERY_EMAIL=reader.one@postbox.zz
BENJI_OWNER_EMAIL=owner@postbox.zz
BENJI_ALLOWED_RECIPIENTS=reader.one@postbox.zz,you@example.com
TELEGRAM_BOT_TOKEN=999999:AAFakeTokenForTheTest_abcdefghij
OWNER_CHAT_ID=7012345678
RAHAT_MAX_TOKENS=100000000
"""
PRIVATE = ("zebulon", "quillfeather", "reader.one@postbox.zz", "aafaketokenforthetest", "7012345678")


def _never_printed(out: str) -> None:
    low = out.lower()
    for word in PRIVATE:
        assert word not in low, "the check printed a private word"


class Repo:
    """A work repo with a bare origin, the private list and .env in place."""

    def __init__(self, tmp: Path):
        self.tmp, self.path, self.remote = tmp, tmp / "work", tmp / "origin.git"
        # Never the outer repo: a pre-push hook runs this pin with git's GIT_* set.
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "RAHAT_PRIVACY_OK"}
        self.env.update(HOME=str(tmp), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git("init", "-q", "--bare", str(self.remote), cwd=tmp)
        self.git("init", "-q", "-b", "main", str(self.path), cwd=tmp)
        self.git("config", "user.name", "Test Owner")
        self.git("config", "user.email", "owner@postbox.zz")
        self.git("config", "commit.gpgsign", "false")
        (self.path / "private").mkdir()
        (self.path / "private" / "privacy_terms.txt").write_text(TERMS)
        (self.path / ".env").write_text(ENV)
        self.commit({".gitignore": "private/\n.env\n"}, "base")
        self.git("remote", "add", "origin", str(self.remote))
        self.push()

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.path, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def commit(self, files: dict, msg: str = "change") -> str:
        for name, text in files.items():
            f = self.path / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
            self.git("add", name)
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def push(self) -> None:
        self.git("push", "-q", "origin", "main")

    def check(self, *args: str, **env: str) -> tuple[int, str]:
        """Run the check; with no args, the way the hook does for `git push origin main`."""
        if not args:
            refs = self.tmp / "refs"
            refs.write_text(f"refs/heads/main {self.git('rev-parse', 'HEAD').strip()} "
                            f"refs/heads/main {self.git('rev-parse', 'origin/main').strip()}\n")
            args = ("--pre-push", "origin", "--refs-file", str(refs))
        p = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.path,
                           env={**self.env, **env}, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def test_a_name_in_an_added_line_blocks_the_push_and_is_never_printed(repo):
    sha = repo.commit({"tests/test_reply.py": 'REPLY = "Understood, Zebulon. Your goal is set."\n'})
    code, out = repo.check()
    assert code == 1, out
    assert sha[:9] in out and "tests/test_reply.py:1" in out and "Z******" in out, out
    _never_printed(out)


def test_a_word_added_then_removed_is_still_published(repo):
    first = repo.commit({"notes.txt": "call Quillfeather back\n"})
    repo.commit({"notes.txt": "call back\n"}, "take the name out")
    code, out = repo.check()
    assert code == 1 and first[:9] in out and "notes.txt:1" in out, out


def test_a_split_string_is_joined_before_matching(repo):
    repo.commit({"t.py": 'needles = ["quill" + "feather", "x"]\n'})
    code, out = repo.check()
    assert code == 1 and "t.py:1" in out, out


def test_env_addresses_tokens_and_chat_ids_block_and_stay_masked(repo):
    repo.commit({"cfg.py": "TOKEN = '999999:AAFakeTokenForTheTest_abcdefghij'\nCHAT = 7012345678\n"},
                "send the digest to reader.one@postbox.zz")
    code, out = repo.check()
    assert code == 1, out
    assert "commit message" in out and ".env BENJI_DELIVERY_EMAIL" in out, out
    assert "cfg.py:1" in out and "<.env TELEGRAM_BOT_TOKEN>" in out, out
    assert "cfg.py:2" in out and ".env OWNER_CHAT_ID" in out, out
    _never_printed(out)


def test_your_commit_address_placeholders_and_plain_settings_are_not_private(repo):
    repo.commit({"a.py": "OWNER = 'owner@postbox.zz'\nSAMPLE = 'you@example.com'\nMAX = 100000000\n"})
    code, out = repo.check()
    assert code == 0, out


def test_a_byline_passes_where_the_list_allows_it_and_nowhere_else(repo):
    repo.commit({"README.md": "Built by Zebulon.\n", "docs/about.md": "Author: Zebulon\n"})
    assert repo.check()[0] == 0
    repo.commit({"tests/t.py": "# by Zebulon\n"})
    code, out = repo.check()
    assert code == 1 and "tests/t.py:1" in out and "README.md" not in out, out


def test_a_short_word_matches_only_whole_and_a_long_one_anywhere(repo):
    repo.commit({"a.py": "flash = ashore = 'cash'\n"})
    assert repo.check()[0] == 0
    repo.commit({"b.txt": "mailbox: zebulonjobs2026\n# Ash said so\n"})
    code, out = repo.check()
    assert code == 1 and "b.txt:1" in out and "b.txt:2" in out, out


def test_a_phone_fragment_matches_with_any_separator(repo):
    repo.commit({"p.txt": "call 555-0199-1234\n(555) 0199\n5550199\n15550199\n"})
    code, out = repo.check()
    assert code == 1 and all(f"p.txt:{n}" in out for n in (1, 2, 3)), out
    assert "p.txt:4" not in out, out              # inside a longer number: not the fragment


def test_a_new_path_is_checked_and_masked(repo):
    repo.commit({"notes/quillfeather_resume.md": "nothing private inside\n"})
    code, out = repo.check()
    assert code == 1 and "(the path itself)" in out, out
    _never_printed(out)


def test_no_list_and_no_env_is_a_notice_and_a_pass(repo):
    (repo.path / "private" / "privacy_terms.txt").unlink()
    (repo.path / ".env").unlink()
    repo.commit({"a.txt": "Zebulon\n"})
    code, out = repo.check()
    assert code == 0 and "nothing to check" in out, out


def test_the_owner_can_pass_a_deliberate_word_and_still_sees_it(repo):
    repo.commit({"a.txt": "Quillfeather\n"})
    code, out = repo.check(RAHAT_PRIVACY_OK="1")
    assert code == 0 and "a.txt:1" in out and "passing anyway" in out, out


def test_what_is_already_public_is_not_checked_again_so_a_scrub_passes(repo):
    repo.commit({"t.py": "REPLY = 'Understood, Zebulon.'\n"})
    repo.push()                                   # public already: fixing forward is the way
    repo.commit({"t.py": "REPLY = 'Understood.'\n"}, "test: neutral wording")
    code, out = repo.check()
    assert code == 0, out


def test_run_by_hand_it_checks_what_head_would_add(repo):
    repo.commit({"a.txt": "Quillfeather\n"})
    none = repo.tmp / "no_refs"
    none.write_text("")
    code, out = repo.check("--pre-push", "origin", "--refs-file", str(none))
    assert code == 1 and "a.txt:1" in out, out


def test_the_tree_audit_lists_what_is_public_now(repo):
    repo.commit({"t.py": "x = 1\nREPLY = 'Understood, Zebulon.'\n", "README.md": "Built by Zebulon\n"})
    repo.push()
    code, out = repo.check("--tree")
    assert code == 1 and "t.py:2" in out and "README.md" not in out, out
    _never_printed(out)


def test_the_hook_reads_the_push_first_and_runs_this_check_before_anything_else():
    hook = HOOK.read_text()
    first_step = hook.index('\nstep "')
    assert hook.index('cat > "$PUSH_REFS"') < first_step          # stdin read before any step
    assert hook[first_step:].startswith('\nstep "private words"')
    assert '--pre-push "$PUSH_REMOTE" --refs-file "$PUSH_REFS"' in hook
