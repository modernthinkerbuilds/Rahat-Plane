#!/usr/bin/env python3
"""rahat_site — flip Rahat to the place the Mac is running from (ADR-019).

    .venv/bin/python scripts/rahat_site.py status                 where we are, what runs, the clocks
    .venv/bin/python scripts/rahat_site.py plan hyderabad         what `switch` would do, nothing done
    .venv/bin/python scripts/rahat_site.py switch hyderabad       do it: active site, geocode, launchd, restarts
    .venv/bin/python scripts/rahat_site.py switch home            back
    .venv/bin/python scripts/rahat_site.py playbook hyderabad     the landing checklist with live values
    .venv/bin/python scripts/rahat_site.py new goa --label Goa --timezone Asia/Kolkata --city Panaji --country IN
    .venv/bin/python scripts/rahat_site.py geocode hyderabad      fill lat/lng from the site's address

Sites live in vault/sites/<name>.json (private, never in git; core/site.py
has the shape). `switch`:
  1. geocodes the site's home address once (OpenStreetMap), if lat/lng are
     empty, and writes them back into the site file;
  2. writes vault/sites/active.json, which every process reads at start;
  3. launchd: unloads the labels the site turns off, loads back the ones a
     previous switch stopped (and any in `services.on`), and restarts the
     always-on services so they read the new site. Jobs it never named
     (an overlay agent's) are left alone;
  4. says whether the Mac's clock matches the site's zone, and prints the
     phone lines (the bridge URL over Tailscale or the LAN).
`--dry-run` prints the plan and changes nothing. Owner-run; it never
touches vault/rahat.db.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core import site as csite  # noqa: E402

BRIDGE_PORT = 5000
RAHAT_LABEL = re.compile(r"^com\.rahat\..+\.plist$")


# ── launchd ─────────────────────────────────────────────────────────
@dataclass
class Action:
    kind: str           # stop | start | restart | keep | missing
    label: str
    why: str


class Launchd:
    """The user's launchd agents, through `launchctl`. `run` is injectable
    (tests hand in a fake)."""

    def __init__(self, agents_dir: Path | None = None, uid: int | None = None,
                 run: Callable[[list[str]], subprocess.CompletedProcess] | None = None):
        self.agents_dir = agents_dir or (Path.home() / "Library" / "LaunchAgents")
        self.uid = uid if uid is not None else os.getuid()
        self._run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True))

    def installed(self) -> dict[str, Path]:
        if not self.agents_dir.is_dir():
            return {}
        out: dict[str, Path] = {}
        for p in sorted(self.agents_dir.iterdir()):
            if RAHAT_LABEL.match(p.name):
                out[p.name[:-len(".plist")]] = p
        return out

    def keep_alive(self, plist: Path) -> bool:
        try:
            with plist.open("rb") as f:
                data = plistlib.load(f)
        except Exception:  # noqa: BLE001
            return False
        ka = data.get("KeepAlive")
        return bool(ka) if not isinstance(ka, dict) else True

    def loaded(self, label: str) -> bool:
        return self._run(["launchctl", "print", f"gui/{self.uid}/{label}"]).returncode == 0

    def bootstrap(self, plist: Path) -> subprocess.CompletedProcess:
        return self._run(["launchctl", "bootstrap", f"gui/{self.uid}", str(plist)])

    def bootout(self, label: str) -> subprocess.CompletedProcess:
        return self._run(["launchctl", "bootout", f"gui/{self.uid}/{label}"])

    def kickstart(self, label: str) -> subprocess.CompletedProcess:
        return self._run(["launchctl", "kickstart", "-k", f"gui/{self.uid}/{label}"])


def plan(site: csite.Site, launchd: Launchd, stopped_before: list[str]) -> list[Action]:
    """What a switch does to launchd for this site."""
    installed = launchd.installed()
    off = set(site.services_off)
    want_on = (set(stopped_before) | set((site.raw.get("services") or {}).get("on") or [])) - off
    actions: list[Action] = []
    for label in sorted(off):
        if label not in installed:
            actions.append(Action("missing", label, "not installed here; nothing to stop"))
        elif launchd.loaded(label):
            actions.append(Action("stop", label, f"the {site.label} site turns it off"))
        else:
            actions.append(Action("keep", label, "already unloaded"))
    for label in sorted(want_on):
        if label not in installed:
            actions.append(Action("missing", label, "not installed here; nothing to start"))
        elif not launchd.loaded(label):
            actions.append(Action("start", label, "stopped by an earlier switch; on at this site"))
    starting = {a.label for a in actions if a.kind == "start"}
    for label, plist in installed.items():
        if label in off or label in starting:
            continue
        if launchd.keep_alive(plist) and launchd.loaded(label):
            actions.append(Action("restart", label, "always on; restarted to read the new site"))
    return actions


def apply(actions: list[Action], launchd: Launchd, *, dry_run: bool, say: Callable[[str], None] = print) -> list[str]:
    """Run the plan; returns the labels now stopped by us. Each line says what happened."""
    installed = launchd.installed()
    stopped: list[str] = []
    for a in actions:
        if a.kind in ("keep", "missing"):
            say(f"  · {a.label}: {a.why}")
            continue
        if dry_run:
            say(f"  ~ would {a.kind} {a.label}: {a.why}")
            if a.kind == "stop":
                stopped.append(a.label)
            continue
        if a.kind == "stop":
            r = launchd.bootout(a.label)
            ok = r.returncode == 0
            say(f"  {'✓' if ok else '✗'} stopped {a.label}" + ("" if ok else f": {(r.stderr or r.stdout or '').strip()}"))
            if ok:
                stopped.append(a.label)
        elif a.kind == "start":
            r = launchd.bootstrap(installed[a.label])
            ok = r.returncode == 0
            say(f"  {'✓' if ok else '✗'} started {a.label}" + ("" if ok else f": {(r.stderr or r.stdout or '').strip()}"))
        elif a.kind == "restart":
            r = launchd.kickstart(a.label)
            ok = r.returncode == 0
            say(f"  {'✓' if ok else '✗'} restarted {a.label}" + ("" if ok else f": {(r.stderr or r.stdout or '').strip()}"))
    return stopped


# ── the machine ─────────────────────────────────────────────────────
def _cmd(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def lan_ip() -> str:
    for dev in ("en0", "en1", "en2"):
        ip = _cmd(["ipconfig", "getifaddr", dev])
        if ip:
            return ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


def tailscale_ip() -> str:
    for binary in ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        ip = _cmd([binary, "ip", "-4"])
        if ip:
            return ip.splitlines()[0].strip()
    return ""


def local_hostname() -> str:
    name = _cmd(["scutil", "--get", "LocalHostName"])
    return f"{name}.local" if name else socket.gethostname()


def machine_zone() -> str:
    """The Mac's zone name (/etc/localtime → /var/db/timezone/zoneinfo/<zone>), else its abbreviation."""
    try:
        target = os.readlink("/etc/localtime")
        m = re.search(r"zoneinfo/(.+)$", target)
        if m:
            return m.group(1)
    except OSError:
        pass
    return datetime.now().astimezone().tzname() or "?"


# ── sites on disk ───────────────────────────────────────────────────
def sites_dir() -> Path:
    d = csite.sites_dir()
    if d is None:                                  # RAHAT_TEST_MODE without a dir: never the live vault
        raise SystemExit("rahat_site needs the live vault (unset RAHAT_TEST_MODE) or RAHAT_SITES_DIR")
    return d


def read_site_file(name: str, d: Path) -> dict | None:
    p = d / f"{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write_site_file(name: str, data: dict, d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def read_active(d: Path) -> dict:
    p = d / csite.ACTIVE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def write_active(name: str, d: Path, stopped: list[str], *, now: datetime | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / csite.ACTIVE_FILE
    p.write_text(json.dumps({"active": name, "since": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
                             "by": "rahat_site.py", "stopped": sorted(set(stopped))}, indent=2) + "\n", encoding="utf-8")
    csite.reset()
    return p


def geocode_site(name: str, d: Path, *, say: Callable[[str], None] = print, geocoder=None) -> bool:
    """Fill lat/lng from the site's home address when they are empty. True when the file has a point after."""
    data = read_site_file(name, d)
    if data is None:
        return False
    home = data.get("home") or {}
    if home.get("lat") is not None and home.get("lng") is not None:
        return True
    address = " ".join(str(home.get("address") or "").split())
    if not address:
        say(f"  · no home point: put the house into home.address in {d / (name + '.json')} and run `geocode {name}`")
        return False
    geocoder = geocoder or (lambda q: __import__("core.geocode", fromlist=["geocode"]).geocode(q))
    hit = geocoder(address)
    if not hit:
        say("  ✗ the address did not geocode (OpenStreetMap found nothing); try the area and city in words")
        return False
    home["lat"], home["lng"] = round(float(hit["lat"]), 5), round(float(hit["lng"]), 5)
    home["geocoded"] = {"display": hit.get("display_name", ""), "at": hit.get("observed_at", ""), "source": hit.get("source_name", "")}
    data["home"] = home
    write_site_file(name, data, d)
    say(f"  ✓ home point set from the address ({hit.get('display_name', '')[:70]})")
    return True


# ── commands ────────────────────────────────────────────────────────
def cmd_status(args) -> int:
    d = sites_dir()
    active = read_active(d)
    site = csite.load(active.get("active") or csite.HOME, d)
    print(f"Site: {site.name} ({site.label})" + (f", since {active['since']}" if active.get("since") else "") + f"  [{d}]")
    print(f"  zone: {site.timezone or 'the machine’s clock'} · the Mac's clock: {machine_zone()}")
    mm = csite.clock_mismatch(site)
    if mm:
        print(f"  ✗ {mm}")
    pt = site.home_point()
    print(f"  home point: {'set' if pt else 'none (Bourdain’s profile decides)'}")
    print(f"  Genie: {'on' if site.genie else 'off'} · off at this site: {', '.join(site.services_off) or 'nothing'}")
    if active.get("stopped"):
        print(f"  stopped by the last switch: {', '.join(active['stopped'])}")
    ld = Launchd()
    inst = ld.installed()
    if inst:
        print("  launchd:")
        for label, plist in inst.items():
            state = "loaded" if ld.loaded(label) else "unloaded"
            kind = "always on" if ld.keep_alive(plist) else "on a clock"
            print(f"    {label:28s} {state:9s} {kind}")
    print(f"  bridge: http://{tailscale_ip() or '<tailscale ip>'}:{BRIDGE_PORT}/hae (Tailscale) · "
          f"http://{lan_ip() or '<lan ip>'}:{BRIDGE_PORT}/hae (this Wi-Fi) · {local_hostname()}")
    files = sorted(p.stem for p in d.glob("*.json") if p.name != csite.ACTIVE_FILE) if d.is_dir() else []
    print(f"  sites on file: {', '.join(files) or 'none (home is built in)'}")
    return 0


def cmd_switch(args, *, dry_run: bool) -> int:
    d = sites_dir()
    name = args.name
    data = read_site_file(name, d)
    if data is None and name != csite.HOME:
        print(f"✗ no site file {d / (name + '.json')}; create it with `new {name} ...`")
        return 2
    site = csite.load(name, d)
    active = read_active(d)
    print(("Plan for" if dry_run else "Switching to") + f" {site.name} ({site.label}), zone {site.timezone or 'the machine’s clock'}")
    if data is not None and not dry_run:
        geocode_site(name, d)
    elif data is not None and site.home_point() is None and (site.home.get("address") or "").strip():
        print("  ~ would geocode the home address")
    ld = Launchd(agents_dir=Path(args.agents_dir) if getattr(args, "agents_dir", None) else None)
    actions = plan(site, ld, list(active.get("stopped") or []))
    print("  launchd:")
    stopped = apply(actions, ld, dry_run=dry_run)
    if not dry_run:
        # what we hold stopped: the earlier list minus what came back now, plus what stopped now
        started = {a.label for a in actions if a.kind == "start"}
        write_active(name, d, sorted((set(active.get("stopped") or []) - started) | set(stopped)))
        print(f"  ✓ active site written: {d / csite.ACTIVE_FILE}")
    else:
        print(f"  ~ would write {d / csite.ACTIVE_FILE}")
    mm = csite.clock_mismatch(site)
    print(f"  clock: {'✗ ' + mm if mm else '✓ the Mac’s clock matches the site (' + machine_zone() + ')'}")
    if mm:
        print(f"    fix: System Settings › General › Date & Time › Set time zone automatically (or: sudo systemsetup -settimezone {site.timezone})")
    print_phone(site)
    return 0


def print_phone(site: csite.Site) -> None:
    ts, lan = tailscale_ip(), lan_ip()
    how = (site.phone or {}).get("bridge") or "tailscale"
    print("  phone (Health Auto Export → the bridge):")
    if how == "tailscale":
        print(f"    URL: http://{ts or '<tailscale ip: not running here>'}:{BRIDGE_PORT}/hae   (set once; the same on every network)")
        if lan:
            print(f"    this Wi-Fi only: http://{lan}:{BRIDGE_PORT}/hae")
    else:
        print(f"    URL: http://{lan or '<lan ip>'}:{BRIDGE_PORT}/hae   (this Wi-Fi; changes with the network)")
        if ts:
            print(f"    over Tailscale: http://{ts}:{BRIDGE_PORT}/hae")
    print(f"    test from the phone's browser: http://{ts or lan or '<ip>'}:{BRIDGE_PORT}/health → {{\"ok\": true, \"site\": \"{site.name}\"}}")


def cmd_playbook(args) -> int:
    d = sites_dir()
    name = args.name or (read_active(d).get("active") or csite.HOME)
    site = csite.load(name, d)
    ts, lan = tailscale_ip(), lan_ip()
    off = ", ".join(site.services_off) or "nothing"
    py = ".venv/bin/python"
    print(f"Playbook · {site.label} ({site.name})")
    print("")
    print("Before you leave (at home, once):")
    print("  1. Tailscale on the Mac and the phone (same account). `tailscale ip -4` on the Mac gives its address.")
    print(f"  2. Health Auto Export → the automation that sends to Rahat → URL http://<tailscale ip>:{BRIDGE_PORT}/hae"
          " (and the location Shortcut, same host). Turn the phone's Wi-Fi off and sync once: if the Mac logs it, it works from anywhere.")
    print(f"  3. {py} scripts/rahat_site.py plan {site.name}   — read what will stop and start.")
    print(f"  4. Put the house into home.address in vault/sites/{site.name}.json; `geocode {site.name}` fills the point.")
    print("")
    print("When you land:")
    print("  1. Mac on the Wi-Fi. Let it set its clock: System Settings › General › Date & Time › Set time zone automatically.")
    print(f"  2. {py} scripts/rahat_site.py switch {site.name}")
    print(f"     stops: {off}; restarts the always-on bots; writes the active site.")
    print(f"  3. Phone: open http://<tailscale ip>:{BRIDGE_PORT}/health → it should say \"site\": \"{site.name}\". Then a manual upload in Health Auto Export.")
    print("  4. Telegram: Miya `status`, Kipchoge `/me`, Bourdain `/where` (or share your location once).")
    print(f"  5. {py} scripts/rahat_site.py status   — everything in one screen; the 08:15 line says if the clock is off.")
    print("")
    print("Coming home:")
    print(f"  1. Wi-Fi, clock back (automatic). 2. {py} scripts/rahat_site.py switch home   3. status.")
    print("")
    print(f"Now: Tailscale {ts or 'not running here'} · this Wi-Fi {lan or '?'} · {local_hostname()} · the Mac's clock {machine_zone()}")
    return 0


def cmd_new(args) -> int:
    d = sites_dir()
    if read_site_file(args.name, d) is not None and not args.force:
        print(f"✗ {d / (args.name + '.json')} exists; --force to overwrite")
        return 2
    data = json.loads(json.dumps(csite.TEMPLATE))
    data["name"] = args.name
    data["label"] = args.label or args.name
    data["timezone"] = args.timezone or ""
    data["home"].update({"address": args.address or "", "city": args.city or "", "region": args.region or "",
                         "country": args.country or ""})
    data["services"]["off"] = [s.strip() for s in (args.off or "").split(",") if s.strip()]
    data["agents"]["genie"] = args.genie != "off"
    p = write_site_file(args.name, data, d)
    print(f"✓ wrote {p}; edit it, then `switch {args.name}`")
    return 0


def cmd_geocode(args) -> int:
    d = sites_dir()
    return 0 if geocode_site(args.name, d) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for verb in ("plan", "switch"):
        s = sub.add_parser(verb)
        s.add_argument("name")
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--agents-dir", help="launchd agents folder (default ~/Library/LaunchAgents)")
    s = sub.add_parser("playbook"); s.add_argument("name", nargs="?")
    s = sub.add_parser("new")
    s.add_argument("name"); s.add_argument("--label"); s.add_argument("--timezone"); s.add_argument("--city")
    s.add_argument("--region"); s.add_argument("--country"); s.add_argument("--address")
    s.add_argument("--off", help="comma-separated launchd labels this site turns off")
    s.add_argument("--genie", choices=["on", "off"], default="on"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("geocode"); s.add_argument("name")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "plan":
        return cmd_switch(a, dry_run=True)
    if a.cmd == "switch":
        return cmd_switch(a, dry_run=a.dry_run)
    if a.cmd == "playbook":
        return cmd_playbook(a)
    if a.cmd == "new":
        return cmd_new(a)
    if a.cmd == "geocode":
        return cmd_geocode(a)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
