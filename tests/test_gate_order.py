"""The gate order is a test (ADR-018 §2.11): manifest allow-list → source
registry → brake → tool call → post-tool provenance and stripping →
verifier → judge → render. A scripted wire, spies on each gate."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("RAHAT_TEST_MODE", "1")

from core import judge as cjudge, llm_chat, tools as ctools, verify as cverify  # noqa: E402
from core.harness import run as hrun  # noqa: E402
from core.harness import render as hrender  # noqa: E402
from agents.bourdain import harness as bh  # noqa: E402
from agents.bourdain import tools as btools  # noqa: E402
from tests.test_harness_loop import FRAME, Wire  # noqa: E402
from tests.test_harness_loop import world  # noqa: E402,F401  (the fixture)

NOW = datetime(2026, 9, 22, 18, 40)


def test_gates_fire_in_the_fixed_order(world, monkeypatch):  # noqa: F811
    seen: list[str] = []
    orig_execute = ctools.ToolRegistry.execute

    def spy_execute(self, name, args, ctx, *, brake=None, cassette_dir=None):
        seen.append("allow_list")           # the registry's first gate
        return orig_execute(self, name, args, ctx, brake=brake, cassette_dir=cassette_dir)

    monkeypatch.setattr(ctools.ToolRegistry, "execute", spy_execute)
    orig_deny = btools._deny_blocked_hosts
    monkeypatch.setattr(btools, "_deny_blocked_hosts", lambda t, a, c: (seen.append("source_registry"), orig_deny(t, a, c))[1])
    orig_check = ctools.Brake.check
    monkeypatch.setattr(ctools.Brake, "check", lambda self, tool: (seen.append("brake"), orig_check(self, tool))[1])
    orig_san = btools._sanitize_values
    monkeypatch.setattr(btools, "_sanitize_values", lambda t, a, r, c: (seen.append("post_tool"), orig_san(t, a, r, c))[1])
    orig_verify = cverify.run
    monkeypatch.setattr(cverify, "run", lambda ctx, reg: (seen.append("verify"), orig_verify(ctx, reg))[1])
    orig_judge = cjudge.judge
    monkeypatch.setattr(cjudge, "judge", lambda *a, **k: (seen.append("judge"), orig_judge(*a, **k))[1])
    orig_render = hrender.render
    monkeypatch.setattr(hrender, "render", lambda *a, **k: (seen.append("render"), orig_render(*a, **k))[1])
    monkeypatch.setattr(llm_chat, "_call_wire", Wire(frame=FRAME, rounds=world["rounds"], answers=[world["good"]],
                                                     judge={"verdicts": [{"outcome": "U1", "verdict": "pass", "line": "ok", "evidence_ids": []}]}))
    res = hrun.run(world["tenant"], "pasta tonight", chat_id="c1", now=NOW, db_path=world["db"])
    assert res.control.verify.ok
    # the tool-call gates once (one tool call), then the answer gates
    assert seen == ["allow_list", "source_registry", "brake", "post_tool", "verify", "judge", "render"], seen


def test_blocked_host_is_refused_before_the_brake_is_charged(world):  # noqa: F811
    reg = btools.build_registry({"now": NOW, "db_path": world["db"], "chat_id": "c1", "profile": world["tenant"].profile()},
                                allow=["read_page_facts"])
    brake = ctools.Brake(max_tool_calls=1)
    res = reg.execute("read_page_facts", {"url": "https://www.yelp.com/biz/x"}, {"now": NOW, "db_path": world["db"]}, brake=brake)
    assert not res.ok and res.denied_by == "_deny_blocked_hosts" and brake.tool_calls == 0 and brake.tripped is None
    res = reg.execute("nope", {}, {}, brake=brake)
    assert res.denied_by == "allow_list" and brake.tool_calls == 0
