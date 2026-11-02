"""Pin (2026-11-02, ADR-019): one switch moves Rahat to another site.

WHY. The Mac mini travels for weeks at a time. Until now every place it
could run from was spelled in code or in launchd: the athlete's zone in
Kipchoge, home in Bourdain's profile, nine jobs that read Bay Area feeds
and gyms whether or not anyone is there, a health line that would nag
about them every morning. Moving meant editing all of it and back.

core/site.py: a site file (vault/sites/<name>.json, private) names the
zone, the home point, the jobs that run there, whether Genie is on, and
env overrides. `scripts/rahat_site.py switch <name>` writes the active
marker, unloads and reloads the launchd jobs, restarts the always-on
bots. Every process reads the site once at start. With no site files, or
under test, Rahat is at home and nothing changes.

Each case below runs against a throwaway sites directory of invented
places; launchctl is a fake.
"""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 20, 8, 15)
AWAY = {
    "label": "Probe City",
    "timezone": "Asia/Kolkata",
    "home": {"address": "Probe Colony, Probe City", "city": "Probe City", "region": "Probe State", "country": "ZZ",
             "radius_min": 20},
    "services": {"off": ["com.rahat.genie", "com.rahat.events", "com.rahat.events.search", "com.rahat.food.nightly",
                         "com.rahat.sugar.bridge"]},
    "agents": {"genie": False},
    "env": {"RAHAT_PROBE_SITE_FLAG": "on"},
}
ALWAYS_ON = ("com.rahat.miya.v2", "com.rahat.genie", "com.rahat.bourdain", "com.rahat.kipchoge", "com.rahat.vitals.v2",
             "com.rahat.sugar.bridge")
ON_A_CLOCK = ("com.rahat.events", "com.rahat.events.search", "com.rahat.food.nightly", "com.rahat.zz_overlay.inbox")


@pytest.fixture
def sites(tmp_path, monkeypatch):
    """A sites directory the site module reads (RAHAT_SITES_DIR), empty."""
    from core import site
    d = tmp_path / "sites"
    d.mkdir()
    monkeypatch.setenv("RAHAT_SITES_DIR", str(d))
    monkeypatch.delenv("RAHAT_SITE", raising=False)
    site.reset()
    yield d
    site.reset()


def _away(d: Path, data: dict | None = None, *, point: bool = True) -> None:
    from core import site
    body = json.loads(json.dumps(data or AWAY))
    if point:
        body["home"]["lat"], body["home"]["lng"] = 17.0, 78.0
    (d / "probe.json").write_text(json.dumps(body))
    site.write_active("probe", d)


def _cli():
    spec = importlib.util.spec_from_file_location("rahat_site", ROOT / "scripts" / "rahat_site.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rahat_site"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeLaunchd:
    """launchctl as a dict: which labels are loaded."""

    def __init__(self, agents_dir: Path):
        self.agents_dir, self.loaded, self.log = agents_dir, set(), []
        for label in ALWAYS_ON:
            self._plist(label, {"Label": label, "KeepAlive": True})
            self.loaded.add(label)
        for label in ON_A_CLOCK:
            self._plist(label, {"Label": label, "StartCalendarInterval": {"Hour": 7}})
            self.loaded.add(label)

    def _plist(self, label, data):
        with (self.agents_dir / f"{label}.plist").open("wb") as f:
            plistlib.dump(data, f)

    def run(self, cmd):
        self.log.append(cmd[1:])
        if cmd[1] == "print":
            return subprocess.CompletedProcess(cmd, 0 if cmd[2].rsplit("/", 1)[1] in self.loaded else 113)
        if cmd[1] == "bootout":
            self.loaded.discard(cmd[2].rsplit("/", 1)[1])
        if cmd[1] == "bootstrap":
            self.loaded.add(Path(cmd[3]).stem)
        return subprocess.CompletedProcess(cmd, 0)


# ── at home, nothing changes ───────────────────────────────────────
def test_without_site_files_rahat_is_at_home_and_nothing_changes(monkeypatch):
    from core import site
    monkeypatch.delenv("RAHAT_SITES_DIR", raising=False)
    monkeypatch.setenv("RAHAT_SITE", "probe")           # ignored under test without a directory
    site.reset()
    s = site.active()
    assert s.is_home and s.timezone is None and s.services_off == () and s.genie and s.checks_off() == frozenset()
    from agents.kipchoge import ingest
    assert ingest.home_zone() == ingest.HOME_ZONE
    from new_plane.miya_runner import agent_boot
    assert agent_boot.production_agents() == agent_boot.PRODUCTION_AGENT_CLASSES and agent_boot.genie_off_line() is None
    from bridges.events.ingest import site_turns_off
    assert site_turns_off("com.rahat.events") is False
    site.reset()


def test_an_unreadable_site_file_runs_as_home_and_never_raises(sites):
    from core import site
    (sites / "probe.json").write_text("{not json")
    site.write_active("probe", sites)
    s = site.active()
    assert s.name == "probe" and s.timezone is None and s.services_off == () and s.genie


# ── away ───────────────────────────────────────────────────────────
def test_the_site_file_decides_zone_point_jobs_genie_and_env(sites, monkeypatch):
    from core import site
    _away(sites)
    s = site.active()
    assert s.name == "probe" and s.label == "Probe City" and s.timezone == "Asia/Kolkata" and not s.genie
    assert s.home_point() == (17.0, 78.0)
    assert s.checks_off() == {"genie_dead_feeds", "genie_feeds_dated_past", "genie_feeds_stale", "food_feeds_silent", "gym_plan_stale"}
    monkeypatch.delenv("RAHAT_PROBE_SITE_FLAG", raising=False)
    assert site.apply_env() == ["RAHAT_PROBE_SITE_FLAG"] and os.environ["RAHAT_PROBE_SITE_FLAG"] == "on"
    monkeypatch.delenv("RAHAT_PROBE_SITE_FLAG", raising=False)


def test_the_health_line_skips_the_jobs_the_site_turns_off_and_names_a_wrong_clock(sites, monkeypatch):
    from core import health, site
    _away(sites)
    monkeypatch.setenv("RAHAT_TEST_VAULT_DIR", str(sites.parent / "vault"))
    (sites.parent / "vault").mkdir(exist_ok=True)
    ran: list[str] = []
    for name in ("model_refusals", "lost_messages", "genie_dead_feeds", "genie_feeds_dated_past", "genie_feeds_stale",
                 "watch_silent", "gym_plan_stale", "food_feeds_silent", "overlay_not_loaded"):
        monkeypatch.setattr(health, name, (lambda n: lambda now, **kw: ran.append(n) or [])(name))
    monkeypatch.setattr(health, "_overlay_checks", lambda: [])
    monkeypatch.setattr(site, "machine_offset_min", lambda at=None: -480)      # the Mac still on Pacific time
    lines = health.findings(NOW)
    assert set(ran) == {"model_refusals", "lost_messages", "watch_silent", "overlay_not_loaded"}, ran
    assert lines == ["The Mac's clock is 13.5 h off Asia/Kolkata, the Probe City site's zone; the scheduled jobs run on the "
                     "Mac's clock. Set the time zone (System Settings › Date & Time)."], lines
    monkeypatch.setattr(site, "machine_offset_min", lambda at=None: 330)       # the clock set right: silence
    assert health.findings(NOW) == []


def test_miya_leaves_genie_out_and_says_so_on_genies_rungs(sites):
    from agents.genie.agent import GenieAgent
    from new_plane.miya_runner import agent_boot
    _away(sites)
    roster = agent_boot.production_agents()
    assert GenieAgent not in roster and set(roster) < set(agent_boot.PRODUCTION_AGENT_CLASSES)
    line = agent_boot.genie_off_line()
    assert line and line.startswith("Genie is off while we're in Probe City") and "home site" in line


def test_kipchoges_clock_follows_the_site(sites):
    from agents.kipchoge import ingest
    from new_plane.kipchoge_runner import bot
    _away(sites)
    assert ingest.home_zone() == "Asia/Kolkata"
    assert ingest.home_offset_min(datetime(2026, 11, 20, 7, 0)) == 330
    assert bot.home_now(datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)) == datetime(2026, 11, 20, 7, 30)


def test_bourdains_home_is_the_sites_point_in_its_zone(sites, monkeypatch, tmp_path):
    from agents.bourdain import profile
    prof = {"members": [{"id": "a"}], "home": {"city": "Hometown", "region": "CA", "tz": "America/Los_Angeles",
                                                 "lat": 37.0, "lng": -122.0, "radius_min": 25, "minutes_mode": "drive"}}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(prof))
    monkeypatch.setenv(profile.ENV_PATH, str(p))
    home = profile.load()["home"]
    assert (home["city"], home["lat"], home["tz"]) == ("Hometown", 37.0, "America/Los_Angeles") and "_site" not in home
    _away(sites)
    home = profile.load()["home"]
    assert (home["city"], home["region"], home["lat"], home["lng"], home["tz"], home["radius_min"], home["minutes_mode"]) == \
        ("Probe City", "Probe State", 17.0, 78.0, "Asia/Kolkata", 20, "drive") and home["_site"] == "probe"


def test_a_stays_own_zone_sets_the_runners_clock(monkeypatch):
    from agents.bourdain import resolve, state
    from new_plane.bourdain_runner import bot
    monkeypatch.setattr(state, "trip", lambda: {"stays": {"probe": {"dates": "x", "tz": "Asia/Kolkata"}}})
    monkeypatch.setattr(resolve, "leg_for", lambda trip, now: "probe")
    assert bot.trip_now(datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)) == datetime(2026, 11, 20, 7, 30)
    monkeypatch.setattr(state, "trip", lambda: {"stays": {"east": {"dates": "x"}}})
    monkeypatch.setattr(resolve, "leg_for", lambda trip, now: "east")
    assert bot.trip_now(datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)) == datetime(2026, 11, 19, 21, 0)   # TRIP_TZ as before


def test_the_event_and_food_runs_refuse_to_run_at_a_site_that_turns_them_off(sites, capsys):
    from bridges.events.ingest import site_turns_off
    from bridges.food import nightly
    _away(sites)
    assert site_turns_off("com.rahat.events") is True and site_turns_off("com.rahat.vitals.v2") is False
    assert nightly.main(["--feeds"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"skipped": "the site turns the food run off"}


def test_the_bridge_names_the_site(sites):
    from bridges.healthkit import server
    assert server.health()["site"] == "home"
    _away(sites)
    assert server.health() == {"ok": True, "service": "healthkit-bridge", "site": "probe"}


# ── the switch ─────────────────────────────────────────────────────
def test_the_switch_stops_starts_and_restarts_the_right_jobs_there_and_back(sites, tmp_path):
    from core import site
    rs = _cli()
    agents = tmp_path / "agents"
    agents.mkdir()
    fake = FakeLaunchd(agents)
    ld = rs.Launchd(agents_dir=agents, uid=501, run=fake.run)
    _away(sites, point=False)
    away = site.load("probe", sites)
    acts = {(a.kind, a.label) for a in rs.plan(away, ld, [])}
    assert acts == {("stop", "com.rahat.genie"), ("stop", "com.rahat.events"), ("stop", "com.rahat.events.search"),
                    ("stop", "com.rahat.food.nightly"), ("stop", "com.rahat.sugar.bridge"),
                    ("restart", "com.rahat.miya.v2"), ("restart", "com.rahat.bourdain"), ("restart", "com.rahat.kipchoge"),
                    ("restart", "com.rahat.vitals.v2")}, acts            # the overlay's job is never touched
    stopped = rs.apply(rs.plan(away, ld, []), ld, dry_run=False, say=lambda s: None)
    assert sorted(stopped) == sorted(away.services_off)
    assert "com.rahat.zz_overlay.inbox" in fake.loaded and "com.rahat.genie" not in fake.loaded
    rs.write_active("probe", sites, stopped)
    assert json.loads((sites / "active.json").read_text())["stopped"] == sorted(stopped)
    home = site.load("home", sites)
    back = {(a.kind, a.label) for a in rs.plan(home, ld, stopped)}
    assert {k for k, _ in back} == {"start", "restart"} and {l for k, l in back if k == "start"} == set(stopped)
    rs.apply(rs.plan(home, ld, stopped), ld, dry_run=False, say=lambda s: None)
    assert fake.loaded == set(ALWAYS_ON) | set(ON_A_CLOCK)
    # a dry run changes nothing
    before = set(fake.loaded)
    rs.apply(rs.plan(away, ld, []), ld, dry_run=True, say=lambda s: None)
    assert fake.loaded == before and not [c for c in fake.log if c[0] in ("bootout", "bootstrap", "kickstart") and c in fake.log[-9:]]


def test_the_switch_geocodes_the_home_address_once(sites):
    rs = _cli()
    _away(sites, point=False)
    calls: list[str] = []

    def geocoder(q):
        calls.append(q)
        return {"lat": 17.4321, "lng": 78.4123, "display_name": "Probe Colony, Probe City", "observed_at": "t", "source_name": "OSM"}
    assert rs.geocode_site("probe", sites, say=lambda s: None, geocoder=geocoder) is True
    home = json.loads((sites / "probe.json").read_text())["home"]
    assert (home["lat"], home["lng"]) == (17.4321, 78.4123) and home["geocoded"]["display"] == "Probe Colony, Probe City"
    assert rs.geocode_site("probe", sites, say=lambda s: None, geocoder=geocoder) is True and calls == ["Probe Colony, Probe City"]
    from core import site
    site.reset()
    assert site.load("probe", sites).home_point() == (17.4321, 78.4123)


def test_new_writes_a_site_the_module_reads(sites, monkeypatch):
    rs = _cli()
    monkeypatch.setattr(sys, "argv", ["rahat_site"])
    assert rs.main(["new", "goa", "--label", "Goa", "--timezone", "Asia/Kolkata", "--city", "Panaji", "--country", "IN",
                    "--off", "com.rahat.genie,com.rahat.events", "--genie", "off"]) == 0
    from core import site
    s = site.load("goa", sites)
    assert (s.label, s.timezone, s.home["city"], s.services_off, s.genie) == ("Goa", "Asia/Kolkata", "Panaji",
                                                                                ("com.rahat.genie", "com.rahat.events"), False)
    assert rs.main(["new", "goa", "--label", "Goa"]) == 2          # never overwrites without --force
