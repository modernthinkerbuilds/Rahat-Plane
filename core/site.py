"""core.site — where Rahat is running from (2026-11-02, ADR-019).

The Mac mini travels. A *site* is one place it runs from: a time zone, a
home point, the launchd jobs that make sense there, the agents that are
on, and settings that differ. One site is active at a time; every process
reads it once at start, so the owner flips everything with one command
(`scripts/rahat_site.py switch <name>`) and a restart.

Files, all private (vault/ is never in git):

    vault/sites/<name>.json     one site each (see TEMPLATE below)
    vault/sites/active.json     {"active": "<name>", "since": "..."}

Resolution: $RAHAT_SITE, else vault/sites/active.json, else "home". A
site file is merged over DEFAULT; "home" without a file is DEFAULT
itself, which is exactly how Rahat ran before sites existed: every job
on, no home point of its own (Bourdain's profile keeps that), the
machine's clock. Nothing changes for a clone with no site files.

Under RAHAT_TEST_MODE the files are not read unless a test names a
sites directory ($RAHAT_SITES_DIR), so the suite stays hermetic.

What a site decides, and who reads it:
  * `timezone`      the athlete's home clock (Kipchoge), Bourdain's home
                    clock; the health line says when the Mac's clock is
                    on another zone (scheduled jobs run on the Mac's).
  * `home`          Bourdain's "near home": lat/lng (geocoded once from
                    `address` by the switch), city, region, travel mode.
  * `services.off`  launchd labels the switch unloads here; everything
                    else installed stays. core.health skips the checks
                    those jobs would otherwise fail (dead event feeds
                    with the event runs off, the gym plan with the gym
                    bridge off, silent food feeds with the food run off).
                    The event and food runs also refuse to run at a site
                    that turns them off.
  * `agents.genie`  off: Miya does not register Genie and says so on
                    Genie's rungs.
  * `env`           environment overrides applied to every process at
                    start (budget caps, flags), on top of .env.
Never raises.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("rahat.site")

ACTIVE_ENV = "RAHAT_SITE"            # the site's name, for one command or one process
DIR_ENV = "RAHAT_SITES_DIR"          # where the site files are (default vault/sites)
HOME = "home"
ACTIVE_FILE = "active.json"

# The jobs a site may turn off, by launchd label, and the health checks that
# would fail without them (core.health skips those).
CHECKS_BY_SERVICE = {
    "com.rahat.events": ("genie_dead_feeds", "genie_feeds_dated_past", "genie_feeds_stale"),
    "com.rahat.events.search": (),
    "com.rahat.food.nightly": ("food_feeds_silent",),
    "com.rahat.sugar.bridge": ("gym_plan_stale",),
    "com.rahat.genie": (),
}

DEFAULT: dict[str, Any] = {
    "name": HOME,
    "label": "home",
    "timezone": None,                      # None: the machine's clock, each agent's own default
    "home": {},                            # empty: Bourdain's profile decides
    "services": {"off": []},
    "agents": {"genie": True},
    "env": {},
    "phone": {},
    "notes": "",
}

# What `rahat_site.py new` writes. Keys starting with "_" are notes to the owner.
TEMPLATE: dict[str, Any] = {
    "_what": "One place the Mac runs from. Private: vault/ is never in git. Edit, then `scripts/rahat_site.py switch <name>`.",
    "name": "",
    "label": "",
    "timezone": "",
    "_timezone": "IANA zone of the place (Asia/Kolkata). Kipchoge's and Bourdain's clocks; the health line says when the Mac's clock differs.",
    "home": {
        "address": "",
        "_address": "The house, in words (area, city). The switch geocodes it once (OpenStreetMap) and fills lat/lng.",
        "lat": None, "lng": None,
        "city": "", "region": "", "country": "",
        "minutes_mode": "drive", "radius_min": 25,
    },
    "services": {
        "off": [],
        "_off": "launchd labels the switch unloads at this site; everything else installed stays. "
                "Known: com.rahat.genie, com.rahat.events, com.rahat.events.search, com.rahat.food.nightly, com.rahat.sugar.bridge.",
    },
    "agents": {"genie": True, "_genie": "false: Miya does not register Genie here and says so when asked."},
    "env": {"_env": "Environment overrides for every process at this site, on top of .env (budget caps, flags)."},
    "phone": {"bridge": "tailscale", "_bridge": "How the phone reaches the health bridge: tailscale | lan. The playbook prints the URL."},
    "notes": "",
}


@dataclass(frozen=True)
class Site:
    name: str
    label: str
    timezone: str | None
    home: dict
    services_off: tuple[str, ...]
    genie: bool
    env: dict
    phone: dict
    notes: str = ""
    path: str | None = None                 # the file it came from, None for the built-in default
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_home(self) -> bool:
        return self.name == HOME

    def service_on(self, label: str) -> bool:
        return label not in self.services_off

    def checks_off(self) -> frozenset[str]:
        """core.health checks that have nothing to check at this site."""
        out: set[str] = set()
        for label in self.services_off:
            out.update(CHECKS_BY_SERVICE.get(label, ()))
        return frozenset(out)

    def home_point(self) -> tuple[float, float] | None:
        lat, lng = self.home.get("lat"), self.home.get("lng")
        try:
            return (float(lat), float(lng)) if lat is not None and lng is not None else None
        except (TypeError, ValueError):
            return None

    def describe(self) -> str:
        where = self.label or self.name
        tz = self.timezone or "the machine's clock"
        off = ", ".join(self.services_off) if self.services_off else "none"
        return f"site {self.name} ({where}) · zone {tz} · off: {off} · Genie {'on' if self.genie else 'off'}"


# ── where the files are ──────────────────────────────────────────────
def _test_mode() -> bool:
    return os.environ.get("RAHAT_TEST_MODE", "").lower() in ("1", "true", "yes")


def sites_dir() -> Path | None:
    """The site files' directory; None when they must not be read (tests
    without an explicit directory)."""
    explicit = os.environ.get(DIR_ENV)
    if explicit:
        return Path(explicit)
    if _test_mode():
        return None
    from core import io as cio
    return Path(str(cio.DB_PATH)).parent / "sites"


def site_file(name: str, directory: Path | None = None) -> Path | None:
    d = directory if directory is not None else sites_dir()
    return (d / f"{name}.json") if d is not None else None


def _read_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def active_name() -> str:
    """$RAHAT_SITE, else active.json, else home. Tests without a sites
    directory are always at home."""
    d = sites_dir()
    if d is None:
        return HOME
    env = (os.environ.get(ACTIVE_ENV) or "").strip()
    if env:
        return env
    data = _read_json(d / ACTIVE_FILE)
    name = str((data or {}).get("active") or "").strip()
    return name or HOME


def _clean(d: dict) -> dict:
    """A site file without its '_' notes, at every level."""
    out: dict = {}
    for k, v in d.items():
        if str(k).startswith("_"):
            continue
        out[k] = _clean(v) if isinstance(v, dict) else v
    return out


def _merge(base: dict, over: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def build(name: str, data: dict | None, path: Path | None = None) -> Site:
    """A Site from a file's dict (None: the built-in default for that name)."""
    merged = _merge(DEFAULT, _clean(data or {}))
    merged["name"] = name
    if not merged.get("label"):
        merged["label"] = name
    services = merged.get("services") or {}
    off = tuple(str(s) for s in (services.get("off") or []) if str(s).strip())
    agents = merged.get("agents") or {}
    genie = agents.get("genie", True)
    env = {str(k): str(v) for k, v in (merged.get("env") or {}).items() if v is not None}
    tz = (merged.get("timezone") or "").strip() or None
    return Site(name=name, label=str(merged.get("label") or name), timezone=tz, home=dict(merged.get("home") or {}),
                services_off=off, genie=bool(genie) if genie is not None else True, env=env,
                phone=dict(merged.get("phone") or {}), notes=str(merged.get("notes") or ""),
                path=str(path) if path else None, raw=merged)


def load(name: str, directory: Path | None = None) -> Site:
    """The named site: its file merged over the default, or the default when
    there is no file (home) or the file is unreadable (said in the log)."""
    d = directory if directory is not None else sites_dir()
    path = site_file(name, d) if d is not None else None
    data = _read_json(path) if path is not None else None
    if data is None and path is not None and path.exists():
        log.warning("site file %s is not valid JSON; running as the built-in %s", path, name)
    if data is None and name != HOME and path is not None and not path.exists():
        log.warning("no site file for %s at %s; running with the built-in defaults", name, path)
    return build(name, data, path if data is not None else None)


_ACTIVE: Site | None = None


def active() -> Site:
    """The active site, read once per process. Never raises."""
    global _ACTIVE
    if _ACTIVE is None:
        try:
            _ACTIVE = load(active_name())
        except Exception as e:  # noqa: BLE001 — a bad file never takes a bot down
            log.warning("site unreadable (%s); running as home", e)
            _ACTIVE = build(HOME, None)
    return _ACTIVE


def reset() -> None:
    """Forget the cached site (tests; the switch after writing a file)."""
    global _ACTIVE
    _ACTIVE = None


def apply_env() -> list[str]:
    """Put the active site's env overrides into this process's environment.
    Called once at start (core/__init__). Returns the keys set."""
    site = active()
    keys: list[str] = []
    for k, v in site.env.items():
        if not k or not k.isidentifier():
            continue
        os.environ[k] = v
        keys.append(k)
    if keys:
        log.info("site %s set %s", site.name, ", ".join(sorted(keys)))
    return keys


# ── clocks ──────────────────────────────────────────────────────────
def zone_offset_min(zone: str | None, at: datetime | None = None) -> int | None:
    """A zone's UTC offset in minutes at `at` (now), None when unknown."""
    if not zone:
        return None
    try:
        from zoneinfo import ZoneInfo
        off = (at or datetime.now()).replace(tzinfo=ZoneInfo(zone)).utcoffset()
        return int(off.total_seconds() // 60) if off is not None else None
    except Exception:  # noqa: BLE001
        return None


def machine_offset_min(at: datetime | None = None) -> int | None:
    off = (at or datetime.now()).astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off is not None else None


def clock_mismatch(site: Site | None = None, at: datetime | None = None) -> str | None:
    """A line when the Mac's clock and the site's zone disagree (the
    scheduled jobs run on the Mac's clock), else None."""
    site = site or active()
    want, have = zone_offset_min(site.timezone, at), machine_offset_min(at)
    if want is None or have is None or want == have:
        return None
    hours = abs(want - have) / 60.0
    h = f"{hours:g} h" if hours != int(hours) else f"{int(hours)} h"
    return (f"The Mac's clock is {h} off {site.timezone}, the {site.label} site's zone; the scheduled "
            f"jobs run on the Mac's clock. Set the time zone (System Settings › Date & Time).")


# ── the active marker ──────────────────────────────────────────────
def write_active(name: str, directory: Path, *, now: datetime | None = None, by: str = "rahat_site.py") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / ACTIVE_FILE
    p.write_text(json.dumps({"active": name, "since": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"), "by": by},
                            indent=2) + "\n", encoding="utf-8")
    reset()
    return p
