"""Regression pin — 2026-10-16: "/fraser …" reaches Fraser the way "@fraser …" does.

The Telegram menu can only list slash commands, so each agent behind
Miya got one (/kobe, /fraser, /huberman, /miya). Before, any slash
message went to Kobe's slash table, which didn't know "/fraser", and
fell through Kobe's own pipeline: a workout ask answered by the numbers
agent. "/genie …" keeps going to Genie's own command. A group chat's
"@BotName" suffix on the command is peeled."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from new_plane.miya_runner.delegate_classifier import classify_delegation  # noqa: E402


@pytest.mark.parametrize("msg, route, body", [
    ("/fraser design a 45 min session, dumbbells only", "fraser_route", "design a 45 min session, dumbbells only"),
    ("/kobe where am I on pace", "kobe_route", "where am I on pace"),
    ("/huberman 10 minutes, hips", "huberman_route", "10 minutes, hips"),
    ("/miya should I train tomorrow", "orchestrate", "should I train tomorrow"),
    ("/fraser@BadeMiyaBot a quick metcon", "fraser_route", "a quick metcon"),
    ("@fraser a quick metcon", "fraser_route", "a quick metcon"),
])
def test_a_slash_address_routes_like_an_at_address(msg, route, body):
    assert classify_delegation(msg) == (route, body)


def test_genies_own_slash_command_is_untouched():
    assert classify_delegation("/genie plan saturday")[0] == "genie_route"
    assert classify_delegation("/whatson infant friendly")[0] == "genie_route"


def test_kobes_slash_table_still_gets_its_commands():
    assert classify_delegation("/pace")[0] == "kobe_route"
    assert classify_delegation("/week please")[0] == "kobe_route"


def test_a_bare_slash_address_asks_what_you_want():
    assert classify_delegation("/fraser") == ("orchestrate", "/fraser")
