"""Regression pin (2026-11-01) — Kipchoge's installer said "✗ service not
visible" while the bot was running.

LIVE (Nov 1, 12:54 a.m.): `bash scripts/install_kipchoge_bot.sh` loaded
the service, and the bot logged "Kipchoge bot live" the same second, but
the installer ended "✗ service not visible" and exited 1. Its check was
`launchctl list | grep -q com.rahat.kipchoge` under `set -o pipefail`:
`grep -q` exits at the first match, `launchctl list` (hundreds of lines)
dies of SIGPIPE writing the rest, and pipefail reports the pipeline as
failed. Bourdain's, Genie's and Miya's installers had the same check.

THE PIN: no installer that sets pipefail pipes `launchctl list` into
`grep -q`; each asks launchd for its own label
(`launchctl list <label>`, exit 0 when loaded).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPED = re.compile(r"launchctl\s+list\s*\|\s*grep\s+-q")


def test_no_installer_pipes_launchctl_into_grep_q_under_pipefail():
    bad = []
    for sh in sorted((ROOT / "scripts").glob("install_*.sh")):
        text = sh.read_text()
        if "pipefail" in text and PIPED.search(text):
            bad.append(sh.name)
    assert bad == [], f"pipefail + `launchctl list | grep -q` reports a running service as missing: {bad}"


def test_the_bot_installers_ask_launchd_for_their_own_label():
    for name, label in (("install_kipchoge_bot.sh", "com.rahat.kipchoge"), ("install_bourdain_bot.sh", "com.rahat.bourdain"),
                        ("install_genie_bot.sh", "com.rahat.genie"), ("install_new_miya.sh", "com.rahat.miya.v2")):
        assert f"launchctl list {label} >/dev/null 2>&1" in (ROOT / "scripts" / name).read_text(), name
