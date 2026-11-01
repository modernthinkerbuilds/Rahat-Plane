"""Regression (2026-11-01): an agent the owner keeps private runs from the
private overlay, and the public tree never names it.

LIVE: two agents the owner keeps private lived in the public repo with
everything that named them: code, pins, the .env example, a health check
inside core/health.py, an alias in Huberman, names in the voice-leak regex.
Keeping them private meant forking the repo or deleting them.

Fix (core/overlay.py): <repo>/private/overlay (gitignored), or
$RAHAT_PRIVATE_OVERLAY, has the repo's layout and is appended to sys.path
when core is imported. `agents` and `bridges` are namespace packages, so an
overlay's agents/<name>/ joins them; new_plane's __init__ adds the
overlay's new_plane/<name>/. The public tree wins any clash. An overlay with
an __init__.py where the public tree has a namespace package would hide
every public agent, so it is not loaded and the daily health line says so.
core.health runs the HEALTH_CHECKS an overlay agent lists in its health.py.

Each case runs in a fresh interpreter (the way launchd starts a bot) against
a throwaway overlay of invented names.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOW = "datetime(2026, 11, 1, 8, 15)"

PROBE = {
    "agents/zz_probe/__init__.py": "WHO = 'overlay agent'\n",
    "bridges/zz_probe/__init__.py": "WHO = 'overlay bridge'\n",
    "new_plane/zz_probe_runner/__init__.py": "",
    "new_plane/zz_probe_runner/main.py": (
        "WHO = 'overlay runner'\n"
        "if __name__ == '__main__':\n"
        "    import agents.zz_probe, bridges.zz_probe\n"
        "    print('ran', agents.zz_probe.WHO, bridges.zz_probe.WHO)\n"),
    # a clash: the public agents/huberman must win
    "agents/huberman/__init__.py": "raise RuntimeError('the overlay won a clash')\n",
}


def _overlay(tmp_path: Path, files: dict[str, str]) -> Path:
    o = tmp_path / "overlay"
    for rel, body in files.items():
        f = o / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return o


def _run(tmp_path: Path, overlay: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "PYTHONPATH")}
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    env.update(RAHAT_TEST_MODE="1", RAHAT_PRIVATE_OVERLAY=str(overlay), PYTHONDONTWRITEBYTECODE="1",
               RAHAT_TEST_VAULT_DIR=str(vault), RAHAT_EVENTS_DB=str(vault / "events.db"),
               RAHAT_DB_PATH=str(vault / "rahat.db"))
    p = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    return p


def test_the_public_tree_keeps_agents_and_bridges_as_namespace_packages():
    # the overlay joins them only because neither has an __init__.py
    assert not (ROOT / "agents" / "__init__.py").exists()
    assert not (ROOT / "bridges" / "__init__.py").exists()
    assert (ROOT / "new_plane" / "__init__.py").exists()


def test_an_overlay_agent_bridge_and_runner_import_and_the_public_tree_wins_a_clash(tmp_path):
    o = _overlay(tmp_path, PROBE)
    out = _run(tmp_path, o, "-c",
               "import core, importlib.util\n"
               "import agents.zz_probe as a, bridges.zz_probe as b, new_plane.zz_probe_runner.main as r\n"
               "print(a.WHO, '|', b.WHO, '|', r.WHO)\n"
               "print(importlib.util.find_spec('agents.huberman').origin)\n").stdout.splitlines()
    assert out[0] == "overlay agent | overlay bridge | overlay runner", out
    assert out[1] == str(ROOT / "agents" / "huberman" / "__init__.py"), out


def test_a_runner_starts_from_the_overlay_the_way_launchd_starts_it(tmp_path):
    o = _overlay(tmp_path, PROBE)
    out = _run(tmp_path, o, "-m", "new_plane.zz_probe_runner.main").stdout
    assert out.strip() == "ran overlay agent overlay bridge", out


def test_an_overlay_that_would_hide_the_public_agents_is_not_loaded_and_is_said(tmp_path):
    o = _overlay(tmp_path, {**PROBE, "agents/__init__.py": ""})
    out = _run(tmp_path, o, "-c",
               "from datetime import datetime\n"
               "import importlib.util\n"
               "from core import overlay, health\n"
               "print(overlay.refused())\n"
               "print(importlib.util.find_spec('agents.zz_probe'))\n"
               "print(importlib.util.find_spec('agents.huberman').origin)\n"
               f"print(health.overlay_not_loaded({NOW}))\n").stdout.splitlines()
    assert out[0] == "agents/__init__.py would hide the public agents/", out
    assert out[1] == "None", out
    assert out[2] == str(ROOT / "agents" / "huberman" / "__init__.py"), out
    assert "Private agents: not loaded, agents/__init__.py would hide the public agents/" in out[3], out


def test_an_overlay_health_check_joins_the_daily_findings_and_a_broken_one_is_skipped(tmp_path):
    o = _overlay(tmp_path, {
        **PROBE,
        "agents/zz_probe/health.py": "HEALTH_CHECKS = [lambda now: ['Probe: the probe feed is down.']]\n",
        "agents/zz_broken/__init__.py": "",
        "agents/zz_broken/health.py": "raise ImportError('broken on purpose')\n",
    })
    out = _run(tmp_path, o, "-c",
               "from datetime import datetime\n"
               "from core import health\n"
               f"print([c({NOW}) for c in health._overlay_checks()])\n"
               f"print('Probe: the probe feed is down.' in health.findings({NOW}))\n").stdout.splitlines()
    assert out[0] == "[['Probe: the probe feed is down.']]", out
    assert out[1] == "True", out


def test_no_overlay_changes_nothing(tmp_path):
    absent = tmp_path / "no-overlay-here"
    out = _run(tmp_path, absent, "-c",
               "import sys\n"
               "from datetime import datetime\n"
               "from core import overlay, health\n"
               "print(overlay.path(), overlay.activate(), overlay.agents(),"
               " overlay.plugins('health', 'HEALTH_CHECKS'), overlay.refused())\n"
               f"print(any('no-overlay-here' in p for p in sys.path), health.overlay_not_loaded({NOW}))\n"
               ).stdout.splitlines()
    assert out == ["None None [] [] None", "False []"], out
