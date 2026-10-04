"""Service levels from the bot logs (tests.quality_gate.service_levels) — hermetic,
over synthetic log lines shaped like vault/*_bot.log and vault/miya_v2.log."""
from __future__ import annotations

import json
from pathlib import Path

from tests.quality_gate import service_levels as sl

ROOT = Path(__file__).resolve().parents[2]
BOURDAIN = json.loads((ROOT / "agents" / "bourdain" / "manifest.json").read_text())

_BOURDAIN_LOG = """\
2026-10-04 10:43:30,000 INFO bourdain_bot :: Bourdain bot live | primary_chat=1 | pair_code=set | trip=none
2026-10-04 10:48:13,072 INFO bourdain_bot :: [in] chat=1 text='coffee and pastries near me'
2026-10-04 10:49:23,000 INFO bourdain_bot :: [out] chat=1 len=1690
2026-10-04 11:12:26,000 INFO bourdain_bot :: [in] chat=1 text='a cafe to sit in, I can drive 45 min'
2026-10-04 11:12:40,000 INFO bourdain_bot :: [out] chat=1 len=727
2026-10-04 11:35:24,000 INFO bourdain_bot :: [in] chat=1 text="Can you search in the city"
2026-10-04 11:36:10,000 INFO bourdain_bot :: [out] chat=1 len=74
2026-10-04 11:40:00,000 INFO bourdain_bot :: [in] chat=1 text='/list'
2026-10-04 11:40:01,000 INFO bourdain_bot :: [out] chat=1 len=67
"""

_GENIE_LOG = """\
2026-10-04 08:59:56,000 INFO genie_bot :: [in] chat=1 text='/whatson'
2026-10-04 08:59:56,000 INFO genie_bot :: [in] chat=1 text='/whatson'
2026-10-04 09:00:09,000 WARNING new_plane.miya_runner.telegram :: telegram sendMessage failed: HTTPError: HTTP Error 400: Bad Request
2026-10-04 09:00:10,000 INFO genie_bot :: [out] chat=1 len=2876
2026-09-17 01:05:28,000 INFO genie_bot :: [in] chat=1 text='any infant friendly events this weekend?'
2026-09-17 01:05:29,000 INFO genie_bot :: [out] chat=1 len=100
"""

_MIYA_LOG = """\
2026-09-28 08:00:44,830 WARNING new_plane.miya_runner.telegram :: telegram sendMessage failed: URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>
2026-09-28 08:01:14,853 WARNING new_plane.miya_runner.telegram :: telegram sendMessage failed: URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>
2026-09-28 08:01:14,856 INFO new_miya_runner :: [nudge] sent morning_briefing (len=146)
2026-09-28 14:00:20,805 INFO new_miya_runner :: [nudge] sent walk_nudge (len=99)
2026-09-28 21:00:02,484 WARNING new_plane.miya_runner.telegram :: telegram sendMessage failed: HTTPError: HTTP Error 400: Bad Request
2026-09-28 21:00:03,108 INFO new_miya_runner :: [nudge] sent recovery_nudge (len=169)
2026-10-03 20:07:27,339 INFO new_miya_runner :: [in] chat=1 text='If I were to hit 900 a day how much should I burn ?'
2026-10-03 20:07:28,000 INFO new_miya_runner :: [out] trace=miya-x model=deterministic-kobe-route tools=['kobe_route'] arbitration=None
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_bourdain_speed_against_its_own_manifest_budget(tmp_path):
    turns = sl.parse_log(_write(tmp_path, "bourdain_bot.log", _BOURDAIN_LOG))
    assert [t.reply_len for t in turns] == [1690, 727, 74, 67]
    lv = sl.levels(turns, manifest=BOURDAIN)
    assert lv.budget_s == BOURDAIN["latency"]["answer_s_home"] and lv.budget_source == "manifest"
    assert lv.answered == 3                       # /list is a lookup: not held to the research budget
    assert lv.over_budget == 2                    # 70 s and 46 s over 30 s; 14 s within
    assert lv.worst_s == 70 and lv.non_answers == 1
    assert "harness_bridge" in lv.examples[0]
    assert "2/3 over the 30 s budget (manifest)" in lv.line()


def test_genie_refusals_and_non_answers_and_duplicate_lines(tmp_path):
    turns = sl.parse_log(_write(tmp_path, "genie_bot.log", _GENIE_LOG))
    assert len(turns) == 2                        # the doubled [in] line is read once
    lv = sl.levels(turns)
    assert lv.markdown_refused == 1 and lv.non_answers == 1 and lv.budget_source == "proposed"


def test_miya_nudges_logged_sent_are_told_apart(tmp_path):
    log = _write(tmp_path, "miya_v2.log", _MIYA_LOG)
    nudges = sl.parse_nudges(log)
    assert [(n.name, n.lost, n.refused) for n in nudges] == [
        ("morning_briefing", True, False), ("walk_nudge", False, False), ("recovery_nudge", False, True)]
    line = sl.nudge_line("miya", nudges)
    assert "3 logged as sent" in line and "1 refused" in line and "1 never arrived (morning_briefing 09-28 08:01)" in line
    turns = sl.parse_log(log)
    assert len(turns) == 1 and turns[0].model == "deterministic-kobe-route" and turns[0].seconds == 1


def test_eval_cost_against_the_brake():
    rep = {"mode": "replay", "cases": [{"cost_usd": 0.12}, {"cost_usd": 0.61}, {"cost_usd": 0.05}]}
    line = sl.eval_cost_levels(rep, BOURDAIN)
    assert "total $0.78" in line and "1 over the $0.50 brake" in line
    assert "no model spend" in sl.eval_cost_levels({"mode": "stub", "cases": [{"cost_usd": 0}]}, BOURDAIN)


def test_the_cli_reads_logs_and_prints_one_line_per_agent(tmp_path, capsys):
    b = _write(tmp_path, "bourdain_bot.log", _BOURDAIN_LOG)
    m = _write(tmp_path, "miya_v2.log", _MIYA_LOG)
    assert sl.main([str(b), str(m), "--examples"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("bourdain: 4 asks") and "miya nudges: 3 logged as sent" in out
