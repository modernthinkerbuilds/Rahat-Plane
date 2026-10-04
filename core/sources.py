"""core.sources — the data access plane (ADR-018 §2.7, platform PR-D).

Every fetch asks this module first. A host is `allowed`, `blocked`,
`link_only` (shown as a link, never read), `api` (a documented public
API called as documented), or `check_robots` (unknown or unlisted:
robots.txt is read once per host, the verdict cached in
vault/robots_cache.json, in memory under RAHAT_TEST_MODE).

Blocked means dropped. A 403 or 429 from a host blocks that host for the
rest of the day (`block_for_today`), and the trace records why.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

from core import io as cio

REGISTRY_PATH = Path(__file__).resolve().parent / "sources.json"
_LOCK = threading.Lock()
_MEM: dict[str, dict] = {}     # host → {"verdict": allowed|blocked, "checked_at": iso, "why": str}
_LAST_CALL: dict[str, float] = {}


def registry() -> dict:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"hosts": {}, "default_policy": "check_robots", "user_agent": "RahatBot/1.0"}


def user_agent() -> str:
    return registry().get("user_agent", "RahatBot/1.0")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _entry(host: str) -> dict:
    hosts = registry().get("hosts", {})
    if host in hosts:
        return hosts[host]
    bare = host[4:] if host.startswith("www.") else host
    for cand in (bare, "www." + bare):
        if cand in hosts:
            return hosts[cand]
    return {}


def _cache_path() -> Path | None:
    if os.environ.get("RAHAT_TEST_MODE", "").lower() in ("1", "true", "yes"):
        return None
    return Path(str(cio.DB_PATH)).parent / "robots_cache.json"


def _load_cache() -> dict:
    p = _cache_path()
    if p is None:
        return dict(_MEM)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict) -> None:
    p = _cache_path()
    if p is None:
        snapshot = dict(data)
        _MEM.clear(); _MEM.update(snapshot)
        return
    try:
        p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def _robots_allows(url: str, *, fetch_robots) -> tuple[bool, str]:
    """Read robots.txt once per host (cached ~30 days)."""
    host = host_of(url)
    scheme = urlparse(url).scheme or "https"
    with _LOCK:
        cache = _load_cache()
        ent = cache.get(host)
        if ent and ent.get("checked_at", "") >= datetime.now().strftime("%Y-%m-%d")[:7]:
            rp = robotparser.RobotFileParser()
            rp.parse((ent.get("robots_txt") or "").splitlines())
            ok = rp.can_fetch(user_agent(), url) if ent.get("robots_txt") is not None else True
            return ok, "robots.txt (cached)"
        text = fetch_robots(f"{scheme}://{host}/robots.txt")
        cache[host] = {"checked_at": datetime.now().strftime("%Y-%m-%d"), "robots_txt": text}
        _save_cache(cache)
    if text is None:
        return True, "no robots.txt"
    rp = robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    return rp.can_fetch(user_agent(), url), "robots.txt"


def _default_fetch_robots(url: str) -> str | None:
    """robots.txt through the HTTP cassette: recorded live, replayed
    hermetically; in test mode with no cassette the host is treated as
    having no robots.txt rather than touching the network."""
    from core import httpcache
    try:
        fetched = httpcache.get(url, headers={"User-Agent": user_agent()}, timeout=8)
    except httpcache.HermeticMiss:
        return None
    if not fetched.ok or fetched.status != 200:
        return None
    return fetched.body[:200_000].decode("utf-8", "replace")


def policy_for(url: str, *, fetch_robots=None) -> dict:
    """{"policy": allowed|blocked|link_only|api, "why": str, "trust_tier": str}."""
    host = host_of(url)
    if not host:
        return {"policy": "blocked", "why": "no host", "trust_tier": ""}
    ent = _entry(host)
    tier = ent.get("trust_tier", "")
    pol = ent.get("policy") or registry().get("default_policy", "check_robots")
    today_block = _load_cache().get(f"block:{host}")
    if today_block and today_block.get("until", "") >= datetime.now().strftime("%Y-%m-%d"):
        return {"policy": "blocked", "why": f"blocked today: {today_block.get('why')}", "trust_tier": tier}
    if pol in ("allowed", "blocked", "link_only", "api"):
        return {"policy": pol, "why": ent.get("notes", "registry"), "trust_tier": tier}
    ok, why = _robots_allows(url, fetch_robots=fetch_robots or _default_fetch_robots)
    return {"policy": "allowed" if ok else "blocked", "why": why, "trust_tier": tier}


def explicit_policy(url: str) -> dict | None:
    """The registry's own verdict (listed policy or a block recorded
    today) without touching robots.txt; None when the host is unlisted.
    The pre-tool gate uses this so a lookup never poisons the robots
    cache."""
    host = host_of(url)
    if not host:
        return {"policy": "blocked", "why": "no host", "trust_tier": ""}
    today_block = _load_cache().get(f"block:{host}")
    if today_block and today_block.get("until", "") >= datetime.now().strftime("%Y-%m-%d"):
        return {"policy": "blocked", "why": f"blocked today: {today_block.get('why')}", "trust_tier": ""}
    ent = _entry(host)
    pol = ent.get("policy")
    if pol in ("allowed", "blocked", "link_only", "api"):
        return {"policy": pol, "why": ent.get("notes", "registry"), "trust_tier": ent.get("trust_tier", "")}
    return None


def block_for_today(url: str, why: str) -> None:
    host = host_of(url)
    with _LOCK:
        cache = _load_cache()
        cache[f"block:{host}"] = {"until": datetime.now().strftime("%Y-%m-%d"), "why": why}
        _save_cache(cache)


def throttle(url: str) -> None:
    """Honor a per-host rate limit from the registry (e.g. Nominatim's 1/s)."""
    host = host_of(url)
    limit = _entry(host).get("rate_limit_per_s")
    if not limit:
        return
    min_gap = 1.0 / float(limit)
    with _LOCK:
        last = _LAST_CALL.get(host, 0.0)
        wait = last + min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[host] = time.monotonic()


def is_link_only(url: str) -> bool:
    return policy_for(url, fetch_robots=lambda u: None)["policy"] == "link_only"

