"""The slash-command menu each bot shows when "/" is typed in Telegram
(new_plane/miya_runner/menu.py, 2026-10-16). Every entry is a command the
bot routes; the lists obey Telegram's limits; a runner publishes only
when Telegram's copy differs."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from new_plane.miya_runner import menu  # noqa: E402
from new_plane.miya_runner import delegate_classifier as dc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _literals(*paths: str) -> set[str]:
    """Slash commands a source file routes by literal ("/x", startswith("/x"), == "/x")."""
    out: set[str] = set()
    for p in paths:
        src = (REPO / p).read_text()
        out |= set(re.findall(r'"/([a-z_]+)', src))
    return out


def test_every_menu_obeys_telegrams_limits():
    for bot, items in menu.MENUS.items():
        assert items and menu.problems(items) == [], bot
        assert len(items) <= 25, f"{bot}: a menu longer than a phone screen is not a hint"
        for name, desc in items:
            assert len(desc) <= 90, f"{bot}/{name}: keep the hint to one line"


def test_bade_miyas_menu_is_what_miya_routes():
    from agents.the_scientist import handler as kobe
    kobe_cmds = {k.lstrip("/") for k in kobe.SLASH_COMMANDS} | {"fix", "pain", "profile"}
    genie_cmds = set(re.search(r"\(genie\|([^)]*)\)", dc._GENIE_SLASH_RE.pattern).group(1).split("|")) | {"genie"}
    aliases = {"kobe", "fraser", "huberman", "miya"}
    routed = kobe_cmds | genie_cmds | aliases
    for name, _ in menu.MIYA:
        assert name in routed, f"/{name} is in Bade Miya's menu but nothing routes it"
    assert {"pace", "week", "fraser", "whatson", "weekend_plan"} <= {n for n, _ in menu.MIYA}


def test_genies_menu_is_what_the_genie_bot_routes():
    genie_cmds = set(re.search(r"\(genie\|([^)]*)\)", dc._GENIE_SLASH_RE.pattern).group(1).split("|")) | {"genie"}
    bot_cmds = _literals("new_plane/genie_runner/bot.py")
    for name, _ in menu.GENIE:
        assert name in genie_cmds | bot_cmds, f"/{name} is in Genie's menu but nothing routes it"


def test_bourdains_menu_is_what_the_bourdain_bot_routes():
    routed = _literals("agents/bourdain/handler.py", "new_plane/bourdain_runner/bot.py", "core/inbound.py")
    for name, _ in menu.BOURDAIN:
        assert name in routed, f"/{name} is in Bourdain's menu but nothing routes it"
    assert {"eat", "kb", "who", "list"} <= {n for n, _ in menu.BOURDAIN}


class _Tg:
    def __init__(self, current):
        self.current = current
        self.set_calls: list = []
        self.ok = True

    def get_my_commands(self):
        return self.current

    def set_my_commands(self, commands):
        self.set_calls.append(commands)
        return self.ok


def test_publish_sets_only_when_telegrams_copy_differs():
    logs: list[str] = []
    tg = _Tg(current=[])
    assert menu.publish(tg, "bourdain", log=logs.append) == "set"
    assert tg.set_calls == [menu.as_commands(menu.BOURDAIN)] and "set 12 commands" in logs[-1]
    tg2 = _Tg(current=menu.as_commands(menu.BOURDAIN))
    assert menu.publish(tg2, "bourdain", log=logs.append) == "unchanged" and tg2.set_calls == []
    tg3 = _Tg(current=[])
    tg3.ok = False
    assert menu.publish(tg3, "genie", log=logs.append).startswith("failed")

    class Boom:
        def get_my_commands(self):
            raise OSError("no network")
    assert menu.publish(Boom(), "miya", log=logs.append).startswith("failed: OSError")


def test_the_telegram_client_reads_and_sets_the_menu(monkeypatch):
    from new_plane.miya_runner import telegram as tgm
    tg = tgm.TelegramClient("123:abc")
    monkeypatch.setattr(tg, "_http", lambda method, params=None, **kw: {"ok": True, "result": [
        {"command": "eat", "description": "Where to eat", "extra": 1}]} if method == "getMyCommands" else {})
    assert tg.get_my_commands() == [{"command": "eat", "description": "Where to eat"}]
    posted: list = []
    monkeypatch.setattr(tg, "_http_post", lambda method, body: (posted.append((method, body)), {"ok": True})[1])
    assert tg.set_my_commands([{"command": "eat", "description": "Where to eat"}]) is True
    assert posted == [("setMyCommands", {"commands": [{"command": "eat", "description": "Where to eat"}]})]
