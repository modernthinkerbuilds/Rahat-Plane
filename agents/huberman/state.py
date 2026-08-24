"""agents.huberman.state — profile, variety memory, autocool marker.

Huberman S1 (2026-08-24). Storage layout:

  vault/huberman_profile.json   athlete profile (hotspots, equipment,
                                active issues). PII — vault only, never
                                in the repo. This module ships a
                                PII-FREE default so a fresh clone still
                                composes a sensible generic cooldown.
  vault/huberman_state.json     the agent's own memory: recently used
                                drills (the variety rule) + the daily
                                autocool sent-marker.
  vault/huberman/*.txt|*.md     optional reference corpus (coaching
                                transcripts the owner drops in). Fed to
                                the LLM as style reference, truncated.

Test hermeticity: every path resolves inside the sandbox under
RAHAT_TEST_MODE=1 (mirrors genie.state._vault_dir / events.store.db_path
— the 2026-05-08 incident guard and its 2026-08-12 events relapse).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# ── vault + paths ─────────────────────────────────────────────────────

def _vault_dir() -> Path:
    if os.getenv("RAHAT_TEST_MODE") == "1":
        sandbox = os.getenv("RAHAT_TEST_VAULT_DIR")
        if sandbox:
            return Path(sandbox)
        import tempfile
        p = Path(tempfile.gettempdir()) / f"rahat_test_{os.getpid()}"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(os.getenv("RAHAT_VAULT_DIR", "vault")).resolve()


def profile_path() -> Path:
    override = os.getenv("HUBERMAN_PROFILE_JSON")
    if override:
        return Path(override).resolve()
    return _vault_dir() / "huberman_profile.json"


def store_path() -> Path:
    override = os.getenv("HUBERMAN_STORE_JSON")
    if override:
        return Path(override).resolve()
    return _vault_dir() / "huberman_state.json"


def corpus_dir() -> Path:
    return _vault_dir() / "huberman"


def db_path() -> str:
    """The health substrate (workouts_hk / raw_vitals / sleep_sessions).
    READ-ONLY from this agent. Same resolution ladder as
    bridges.events.store.db_path — explicit env → TEST-MODE SANDBOX →
    live vault DB. The sandbox branch is load-bearing: a context read
    that falls through to the live DB under tests is the exact class
    the 2026-08-12 events gate failure caught."""
    explicit = os.getenv("RAHAT_HUBERMAN_DB")
    if explicit:
        return explicit
    if os.getenv("RAHAT_TEST_MODE") == "1":
        return str(_vault_dir() / "huberman_test_health.db")
    return os.getenv("RAHAT_VITALS_DB",
                     os.path.expanduser(
                         "~/developer/agency/rahat/vault/rahat.db"))


# ── profile ───────────────────────────────────────────────────────────
# PII-free defaults: generic areas, no equipment (→ bodyweight drills),
# no active issues. The real profile lives in the vault.
DEFAULT_PROFILE: dict = {
    "persona": "mobility_coach",
    "default_minutes": 15,
    "hotspots": [],
    "equipment": [],
    "issues": [],
    "avoid_tags": [],
    "preferences": {"variety_days": 4, "include_transitions": True},
}


def load_profile() -> dict:
    """Vault profile over defaults. Derives `avoid_tags` from any active
    issues so protocols.eligible gets hard filters even when the vault
    file only states the issue in prose fields."""
    prof = dict(DEFAULT_PROFILE)
    try:
        raw = json.loads(profile_path().read_text())
        if isinstance(raw, dict):
            prof.update(raw)
    except Exception:
        pass
    tags = set(prof.get("avoid_tags") or [])
    for issue in prof.get("issues") or []:
        for t in issue.get("avoid_tags") or []:
            tags.add(t)
    prof["avoid_tags"] = sorted(tags)
    return prof


# ── the agent's own memory ────────────────────────────────────────────

def _load_store() -> dict:
    try:
        raw = json.loads(store_path().read_text())
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {"sessions": [], "autocool_sent": {}}


def _save_store(store: dict) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=1))
    tmp.replace(p)


def recent_drills(days: int | None = None,
                  now: datetime | None = None) -> set[str]:
    """Drill keys used in the last `days` days — the variety exclusion
    set. Days defaults to the profile's variety window."""
    if days is None:
        days = int(load_profile().get("preferences", {})
                   .get("variety_days", 4) or 4)
    now = now or datetime.now()
    floor = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    out: set[str] = set()
    for s in _load_store().get("sessions", []):
        if (s.get("date") or "") >= floor:
            out.update(s.get("drills") or [])
    return out


def record_session(drill_keys: list[str],
                   now: datetime | None = None) -> None:
    """Append a used-drill record; keep a rolling 30 days."""
    if not drill_keys:
        return
    now = now or datetime.now()
    store = _load_store()
    store.setdefault("sessions", []).append(
        {"date": now.strftime("%Y-%m-%d"),
         "ts": now.strftime("%Y-%m-%d %H:%M"),
         "drills": sorted(set(drill_keys))})
    floor = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    store["sessions"] = [s for s in store["sessions"]
                         if (s.get("date") or "") >= floor]
    _save_store(store)


def autocool_sent(date_str: str) -> bool:
    return bool(_load_store().get("autocool_sent", {}).get(date_str))


def mark_autocool(date_str: str) -> None:
    store = _load_store()
    store.setdefault("autocool_sent", {})[date_str] = True
    # Rolling 30 keys is plenty.
    keys = sorted(store["autocool_sent"])[-30:]
    store["autocool_sent"] = {k: True for k in keys}
    _save_store(store)


# ── reference corpus ──────────────────────────────────────────────────

def corpus_excerpt(max_chars: int = 6000) -> str:
    """Concatenated excerpt of any coaching transcripts the owner has
    dropped into vault/huberman/. Empty string when none — the coach
    prompt degrades gracefully."""
    d = corpus_dir()
    if not d.is_dir():
        return ""
    chunks: list[str] = []
    used = 0
    for f in sorted(d.glob("*")):
        if f.suffix.lower() not in (".txt", ".md"):
            continue
        try:
            text = f.read_text(errors="ignore").strip()
        except Exception:
            continue
        take = text[: max(0, max_chars - used)]
        if not take:
            break
        chunks.append(take)
        used += len(take)
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(chunks)


# ── shared sqlite helper for context.py ───────────────────────────────

def connect() -> sqlite3.Connection:
    return sqlite3.connect(db_path())
