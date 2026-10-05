"""Regression (2026-10-05, QA-2026-10-04-01): the client never sends Telegram a
message its legacy Markdown refuses.

LIVE: TelegramClient.send_message sent each reply as Markdown first and, after
Telegram's HTTP 400, again as plain text, so a reply with one stray marker
reached the household with every raw * and [here](…) showing (every Genie
/whatson Aug 11 → Oct 4; Miya's 9 pm nudge on CrossFit days). Fix:
repair_legacy_markdown walks each chunk the way Telegram's parser does and
escapes only what it would trip on (an opener that never closes, an underscore
inside a word, a marker inside a bare link), then sends once.
"""
from __future__ import annotations

import logging
import random

from new_plane.miya_runner.telegram import TelegramClient, repair_legacy_markdown
from tests.telegram_checks import legacy_markdown_problems


class _Telegram:
    """Refuses invalid legacy Markdown the way Telegram does (the client's
    _http_post hands back Telegram's HTTP 400 answer)."""

    def __init__(self, refuse_markdown: bool = False):
        self.calls, self.refuse_markdown = [], refuse_markdown

    def post(self, method, body):
        self.calls.append(dict(body))
        if body.get("parse_mode") == "Markdown" and (self.refuse_markdown or legacy_markdown_problems(body["text"])):
            return {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}
        return {"ok": True, "result": {"message_id": len(self.calls)}}


def _client(monkeypatch, fake):
    tg = TelegramClient("t0k3n")
    monkeypatch.setattr(tg, "_http_post", fake.post)
    return tg


def test_a_stray_marker_goes_out_once_with_the_formatting_kept(monkeypatch, caplog):
    fake = _Telegram()
    with caplog.at_level(logging.INFO):
        _client(monkeypatch, fake).send_message(
            "1", "🌙 *9pm check* — CrossFit day.\nAdd a 20-min walk, or log it to workout_log if you trained off-watch.")
    assert len(fake.calls) == 1, fake.calls                      # one send, no refused send + raw retry
    sent = fake.calls[0]["text"]
    assert fake.calls[0]["parse_mode"] == "Markdown" and not legacy_markdown_problems(sent)
    assert "*9pm check*" in sent and "workout\\_log" in sent
    assert any("markdown_repaired=1" in r.getMessage() for r in caplog.records)


def test_what_already_parses_is_sent_as_written():
    for text in ("*bold* and _italic_ and `code_with_underscores` and [a link](https://x.org/a_b)",
                 "escaped: workout\\_log and 2\\*2 and \\[not a link",
                 "```\npre_block with *stars* and _underscores_\n```",
                 "_Live search is unavailable right now._ `/weekend_plan` for a plan."):
        assert repair_legacy_markdown(text) == (text, 0), text


def test_the_live_refusals_are_repaired():
    for text in ("_Primary can `/household remove <chat_id>`._",
                 "[here](https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZ_Rd)_x",
                 "*bold never closes", "``` a pre block that never closes",
                 "[a link](https://example.com/that never closes",
                 "the board: https://scores.example.com/?type=for_time_high&title=a_b"):
        out, escaped = repair_legacy_markdown(text)
        assert escaped and not legacy_markdown_problems(out), (text, out)


def test_any_text_comes_out_valid_and_a_second_repair_changes_nothing():
    rng = random.Random(20261005)
    alphabet = "ab _*`[]()\\\nhttps://x.y/_é"
    for _ in range(5000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 48)))
        out, _ = repair_legacy_markdown(text)
        assert not legacy_markdown_problems(out), (text, out)
        assert repair_legacy_markdown(out) == (out, 0), (text, out)


def test_if_telegram_still_refuses_the_retry_is_the_text_as_written(monkeypatch):
    fake = _Telegram(refuse_markdown=True)
    _client(monkeypatch, fake).send_message("1", "log it to workout_log")
    assert [c.get("parse_mode") for c in fake.calls] == ["Markdown", None]
    assert fake.calls[1]["text"] == "log it to workout_log"      # no backslashes in plain text
