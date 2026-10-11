"""Regression (2026-10-11): Benji never sends mail to the account it sends
from when another account can send it, and setting its sender is safe.

LIVE: with delivery moved into her job-search mailbox, Benji sent from
that same mailbox to itself. Gmail files self-sent mail as already read,
with no notification, so the digest arrived with no alert and went
unread. The owner chose to send from his own Gmail account into her
job-search mailbox; then his health line (to his own address) would be
self-sent in turn.

Fix: emailer.sending_account() sends a message whose recipient IS the
sending account from the mailbox Benji reads instead, when that is a
different account. scripts/benji_set_sender.py sets the sending account:
the app password is typed hidden, checked against Gmail's login first
(nothing sent), and only then written to .env, with a backup; a refused
login or a malformed password changes nothing.

Invented addresses and passwords throughout.
"""
from __future__ import annotations

import importlib.util
import smtplib
from pathlib import Path

import pytest

OWNER = "owner.sender@example.com"        # Benji sends from here
SEARCH = "reader.jobsearch@example.com"   # she reads here; Benji reads replies here
GOOD = "abcdefghijklmnop"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RAHAT_TEST_MODE", "1")
    monkeypatch.setenv("BENJI_SMTP_USER", OWNER)
    monkeypatch.setenv("BENJI_SMTP_APP_PASSWORD", "ownerpw")
    monkeypatch.setenv("BENJI_IMAP_USER", SEARCH)
    monkeypatch.setenv("BENJI_IMAP_APP_PASSWORD", "searchpw")
    monkeypatch.setenv("BENJI_DELIVERY_EMAIL", SEARCH)
    monkeypatch.setenv("BENJI_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("BENJI_ALLOWED_RECIPIENTS", SEARCH)
    import agents.benji  # noqa: F401  (registers the recipient policy)
    from new_plane.benji_runner import emailer
    return emailer


def test_her_digest_goes_from_the_sending_account(env):
    assert env.sending_account(SEARCH) == (OWNER, "ownerpw", False)


def test_the_owners_health_line_is_not_self_sent(env, monkeypatch):
    assert env.sending_account(OWNER) == (SEARCH, "searchpw", True)
    used, sent = [], []
    monkeypatch.setattr(env, "_smtp_transport_for",
                        lambda user, pw: (used.append(user), sent.append)[1])
    monkeypatch.setattr(env, "_smtp_transport", lambda: pytest.fail("self-send"))
    ok, _ = env.send_email(subject="Benji · health", body="x", to=OWNER)
    assert ok and used == [SEARCH] and sent[0]["From"] == f"Benji <{SEARCH}>"
    assert sent[0]["Reply-To"] is None                     # replies go to the mailbox Benji reads


def test_with_one_mailbox_for_everything_there_is_no_other_way(env, monkeypatch):
    monkeypatch.setenv("BENJI_SMTP_USER", SEARCH)
    assert env.sending_account(SEARCH)[0] == SEARCH and not env.sending_account(SEARCH)[2]


# ── the setup script ──
def _script():
    spec = importlib.util.spec_from_file_location("benji_set_sender", ROOT / "scripts" / "benji_set_sender.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Gmail:
    """smtplib.SMTP_SSL stand-in: accepts only GOOD for OWNER."""

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if (user, password) != (OWNER, GOOD):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 rejected")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=k\nBENJI_SMTP_USER=" + SEARCH +
                                   "\nBENJI_SMTP_APP_PASSWORD=searchpw\nBENJI_IMAP_USER=" + SEARCH + "\n")
    return tmp_path


def test_the_password_is_checked_before_anything_is_written(repo, capsys):
    s = _script()
    before = (repo / ".env").read_text()
    assert s.main([OWNER], ask=lambda _p: "wrong password!!", smtp=_Gmail, root=repo) == 1
    assert s.main([OWNER], ask=lambda _p: "zzzz zzzz zzzz zzzz", smtp=_Gmail, root=repo) == 1
    assert (repo / ".env").read_text() == before and not list(repo.glob(".env.bak-*"))
    assert "Nothing changed" in capsys.readouterr().out


def test_an_accepted_login_sets_the_sender_and_keeps_a_backup(repo, capsys):
    s = _script()
    assert s.main([OWNER], ask=lambda _p: "abcd efgh ijkl mnop", smtp=_Gmail, root=repo) == 0
    env = (repo / ".env").read_text()
    assert f"BENJI_SMTP_USER={OWNER}\n" in env and f"BENJI_SMTP_APP_PASSWORD={GOOD}\n" in env
    assert f"BENJI_IMAP_USER={SEARCH}" in env and "GEMINI_API_KEY=k" in env   # the rest untouched
    assert len(list(repo.glob(".env.bak-*"))) == 1
    assert GOOD not in capsys.readouterr().out                                # never printed
