"""agents.bourdain.profile — the household profile as data (BR-S1,
platform PR-S4). Read whole on every ask; never a summary.

The file lives in `vault/bourdain/profile.json` (gitignored). The
public repo carries only this loader, the schema below, and a
PII-free default with roles and no rules, which tests use. Members are
roles, never names (platform PR-S8).

Schema (version 1):
{
  "version": 1,
  "members": {
    "<member_id>": {
      "role": "primary | spouse | toddler | infant | senior | guest",
      "diet": {"avoid": [...], "ok": [...], "vegetarian": bool, "eggs": bool,
               "chicken": "yes | occasionally | no", "seafood_rooms": "ok | only_alone | hard_no",
               "full_main_required": bool, "cuisine_out_exclusions": [...],
               "gf": {"scope": ["pastry", "pizza", "bread", "pasta"], "severity": "preference | allergy",
                      "shared_kitchen_ok": bool}},
      "loves": {"cuisines": [...], "dishes": [...]},
      "coffee": {"times": ["06:30-07:00", "15:00-17:00"], "roaster_preferred": bool,
                 "gf_pastry_required": bool, "never_chain": bool, "morning_near_stay": bool},
      "sweets": {"dessert_after_lunch": bool, "gelato_after_dinner": bool},
      "room": {"calm": bool, "stroller_room": bool, "never_bar": bool, "earlier_tables": bool,
               "walk_minutes_max": int},
      "rules": "unknown"            # a member whose rules are not yet known
    }
  },
  "household": {
    "rules": [{"id": "...", "text": "...", "source": "...", "scope": "standing"}],
    "rating": {"floor": 4.0, "target": 4.3, "min_reviews": 30, "trusted_source": "yelp", "trusted_floor": 4.3},
    "price": {"max_level": "$$", "mains_cap_usd": 20, "shared_plate_flag_usd": 25},
    "chains": {"label_from_locations": 2, "exclude_from_locations": 20},
    "variety_days": 14
  },
  "home": {"city": "...", "region": "...", "tz": "America/Los_Angeles", "radius_min": 25,
           "minutes_mode": "drive", "lat": null, "lng": null}
}
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core import io as cio

ENV_PATH = "RAHAT_BOURDAIN_PROFILE_JSON"

DEFAULT_PROFILE: dict[str, Any] = {
    "version": 1,
    "members": {
        "primary": {"role": "primary", "diet": {}, "loves": {}, "rules": "unknown"},
        "spouse": {"role": "spouse", "diet": {}, "loves": {}, "rules": "unknown"},
        "infant": {"role": "infant", "room": {"calm": True, "stroller_room": True, "never_bar": True,
                                              "earlier_tables": True}},
    },
    "household": {
        "rules": [{"id": "no_bars", "text": "no bar-led or drinking-led rooms", "source": "default",
                   "scope": "standing"}],
        "rating": {"floor": 4.0, "target": 4.3, "min_reviews": 30, "trusted_source": "yelp",
                   "trusted_floor": 4.3},
        "price": {"max_level": "$$", "mains_cap_usd": 20, "shared_plate_flag_usd": 25},
        "chains": {"label_from_locations": 2, "exclude_from_locations": 20},
        "variety_days": 14,
    },
    "home": {"city": "", "region": "", "tz": "America/Los_Angeles", "radius_min": 25,
             "minutes_mode": "drive", "lat": None, "lng": None},
}


def profile_path() -> Path:
    env = os.getenv(ENV_PATH)
    if env:
        return Path(env)
    return Path(str(cio.DB_PATH)).parent / "bourdain" / "profile.json"


def load() -> dict:
    """The canonical profile, whole. Falls back to the PII-free default
    (and says so in `_source`) when the vault file is absent or broken."""
    p = profile_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "members" not in data:
            raise ValueError("profile has no members")
        data.setdefault("household", {})
        data.setdefault("home", {})
        for k, v in DEFAULT_PROFILE["household"].items():
            data["household"].setdefault(k, v)
        data["_source"] = str(p)
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        d = json.loads(json.dumps(DEFAULT_PROFILE))
        d["_source"] = "default (no profile file)"
        return d


def member_ids(profile: dict) -> list[str]:
    return list(profile.get("members", {}).keys())


def diners_default(profile: dict) -> list[str]:
    """Who eats when the ask doesn't say: the adults and the infant (BR-P4)."""
    out = [m for m, v in profile.get("members", {}).items() if v.get("role") in ("primary", "spouse")]
    out += [m for m, v in profile.get("members", {}).items() if v.get("role") == "infant"]
    return out


# What a reply calls a member who isn't the one asking, when the profile
# gives no "label" of its own. Code and tests speak of roles; the
# household reads words.
DEFAULT_LABELS = {"primary": "your partner", "spouse": "your partner", "infant": "baby", "toddler": "toddler",
                  "senior": "grandparent", "guest": "guest"}


def labels(profile: dict, speaker: str | None, diners: list[str] | None = None) -> dict[str, str]:
    """Member id → the word a reply uses: "you" for the one asking, else
    the member's own "label" in the profile, else a word for the role.
    Diners who aren't members (guests) are named by their role word."""
    members = profile.get("members") or {}
    seniors = [mid for mid, m in members.items() if (m.get("role") or mid) == "senior"]
    out: dict[str, str] = {}
    for mid in list(members) + [d for d in (diners or []) if d not in members]:
        m = members.get(mid) or {}
        role = m.get("role") or ("guest" if mid not in members else mid)
        if mid == speaker:
            out[mid] = "you"
        elif str(m.get("label") or "").strip():
            out[mid] = str(m["label"]).strip()
        elif role == "senior" and len(seniors) > 1:
            out[mid] = f"grandparent {seniors.index(mid) + 1}"
        else:
            out[mid] = DEFAULT_LABELS.get(role, mid.replace("_", " "))
    return out


def rules_text(profile: dict, members: list[str]) -> str:
    """The rules for the people at the table, rendered whole for the
    executor's prompt (F37: never a summary)."""
    lines = []
    for mid in members:
        m = profile.get("members", {}).get(mid)
        if not m:
            lines.append(f"- {mid}: a guest; rules as stated for this meal only")
            continue
        lines.append(f"- {mid} ({m.get('role', '?')}): " + json.dumps(
            {k: v for k, v in m.items() if k != "role"}, ensure_ascii=False))
    hh = profile.get("household", {})
    lines.append("- household: " + json.dumps(
        {k: v for k, v in hh.items() if k in ("rules", "rating", "price", "chains", "variety_days")},
        ensure_ascii=False))
    return "\n".join(lines)
