"""benji.protocols — pure types, charter kinds, config loading. No I/O
beyond reading the vault config files whose paths the environment names.

Config doctrine (repo is PUBLIC): the *mechanics* of the Filter Config
and Scoring Rules — geography token lists, title patterns, weights,
bands — are impersonal and live here as defaults. Everything personal —
the org target registry, the dream-org list, the candidate source file,
delivery addresses — lives in vault/benji/* (gitignored) and .env, and
is merged over the defaults at load time. A missing vault file leaves a
runnable engine with placeholder orgs; it never crashes and never leaks.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

# ─────────────────────────── Agent identity ───────────────────────────
AGENT = "benji"

# ─────────────────────────── Charter write kinds ──────────────────────
# Every Benji state write passes core.charter.review() under one of
# these kinds (PRD §5). Declared here even when the writing slice lands
# later (story_log/package are S2) so the vocabulary is stable from S1.
KIND_INVENTORY_UPSERT = "benji.inventory.upsert"
KIND_STATUS_SET = "benji.status.set"
KIND_PACKAGE_GENERATE = "benji.package.generate"
KIND_EMAIL_SEND = "benji.email.send"
KIND_PROFILE_UPDATE = "benji.profile.update"
KIND_STORY_LOG_APPEND = "benji.story_log.append"

ALL_CHARTER_KINDS: tuple[str, ...] = (
    KIND_INVENTORY_UPSERT, KIND_STATUS_SET, KIND_PACKAGE_GENERATE,
    KIND_EMAIL_SEND, KIND_PROFILE_UPDATE, KIND_STORY_LOG_APPEND,
)

# ─────────────────────────── Statuses ─────────────────────────────────
STATUS_NEW = "new"
STATUS_BACKLOG = "backlog"      # cold-start overflow (Tara #1)
STATUS_CLOSED = "closed"        # liveness: gone from its feed (J7)
STATUS_APPLIED = "applied"      # terminal — never resurfaces
STATUS_SKIPPED = "skipped"      # terminal — never resurfaces
STATUS_SNOOZED = "snoozed"

# ─────────────────────────── Score bands ──────────────────────────────
# Scoring Rules v2 "what the score decides": the band decides how much
# gets WRITTEN, never what she sees.
BAND_APPLY = "apply"            # 75+  full package (S2)
BAND_WORTH_A_LOOK = "worth_a_look"  # 60–74  two lines; package on `kit`
BAND_MAYBE = "maybe"            # 45–59  one line, grouped
BAND_SEEN = "seen"              # <45   collapsed count


def band_for(score: int) -> str:
    if score >= 75:
        return BAND_APPLY
    if score >= 60:
        return BAND_WORTH_A_LOOK
    if score >= 45:
        return BAND_MAYBE
    return BAND_SEEN


# PRD precedence rule (v1.1, Tara #3): the Generation Spec coverage
# floor beats the auto-build band. Below this, never generate —
# regardless of score. The digest labels these "stretch — low match".
COVERAGE_FLOOR = 0.60


@dataclass
class FilterOutcome:
    result: str            # "accept" | "reject" | "flag"
    reason: str = ""
    cluster: str = ""      # A–E, "" when rejected before clustering
    flags: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    total: int
    breakdown: dict[str, int]
    band: str
    stretch: bool
    stretch_label: str     # "", "stretch", "stretch — low match"
    rationale: str
    coverage: float
    unmatched: list[str]


# ─────────────────────────── Filter defaults ──────────────────────────
# Impersonal mechanics transcribed from Filter Config v1. Geography is
# public knowledge; the ORG registry is personal and defaults to
# placeholders (real registry: vault/benji/filter_config.json).
BAY_AREA_TOKENS: tuple[str, ...] = (
    "san francisco", "sf bay", "bay area", "oakland", "berkeley",
    "emeryville", "alameda", "san jose", "santa clara", "sunnyvale",
    "mountain view", "palo alto", "east palo alto", "menlo park",
    "redwood city", "san mateo", "foster city", "burlingame",
    "south san francisco", "cupertino", "los altos", "los gatos",
    "campbell", "milpitas", "fremont", "union city", "hayward",
    "san leandro", "richmond, ca", "san rafael", "novato", "petaluma",
    "walnut creek", "pleasanton", "dublin, ca", "livermore", "concord",
    "daly city", "brisbane", "newark, ca",
)

REJECT_CITY_TOKENS: tuple[str, ...] = (
    "new york", "nyc", "brooklyn", "washington dc", "washington, d.c.",
    "arlington va", "boston", "cambridge ma", "chicago", "seattle",
    "bellevue", "portland or", "austin", "dallas", "houston", "atlanta",
    "denver", "boulder", "miami", "philadelphia", "los angeles",
    "santa monica", "san diego", "minneapolis", "nashville", "detroit",
    "phoenix", "salt lake", "london", "dublin ie", "berlin", "amsterdam",
    "paris", "singapore", "sydney", "toronto", "vancouver", "bangalore",
    "bengaluru", "hyderabad", "mumbai", "delhi", "gurgaon",
)

# Bare tokens that are ambiguous without a state ("Newark" is NJ and CA;
# "Richmond" CA/VA; "Washington" state/DC). FLAG, never auto-reject —
# the Filter Config's own careful note, resolved flag-over-reject.
AMBIGUOUS_CITY_TOKENS: tuple[str, ...] = ("newark", "richmond",
                                          "washington")

TITLE_NOUN_RE = (
    r"(program|programs|initiative|initiatives|portfolio|project|impact"
    r"|community|partnership|partnerships|grants|workforce|talent"
    r"|learning|curriculum|education|training|mentorship|mentoring"
    r"|operations|inclusion|accessibility|philanthropy|citizenship"
    r"|social impact|responsible ai|ai governance|policy)")
TITLE_LEVEL_RE = (
    r"(associate|coordinator|specialist|analyst|manager|officer"
    r"|lead|director|consultant|strategist|principal)")

TITLE_EXCLUDE_TOKENS: tuple[str, ...] = (
    "engineer", "engineering", "developer", "software", "infrastructure",
    "devops", "sre", "data scientist", "machine learning engineer",
    "research scientist", "account executive", "sales", "quota", "sdr",
    "bdr", "revenue", "recruiter", "sourcer", "talent acquisition",
    "staffing", "major gifts", "gift officer", "development director",
    "development manager", "annual fund", "donor relations",
    "principal gifts", "grant writer", "foundation relations",
    "advancement", "membership", "clinical", "nurse", "physician",
    "therapist", "counselor", "controller", "accountant", "tax",
    "treasury", "fp&a", "audit", "product marketing", "brand manager",
    "growth marketing", "executive assistant", "administrative assistant",
    "office manager", "warehouse", "driver", "retail", "barista",
    "security guard",
)

# Level guard: hard rejects (Filter Config §2). "Director" alone stays —
# Program Director is Cluster A; only the senior-executive band rejects.
LEVEL_REJECT_TOKENS: tuple[str, ...] = (
    "intern", "apprentice", "vp ", "vp,", "vice president", "chief",
    "head of", "senior director", "managing director",
)
# "Program Assistant is a step down from Program Associate" — reject.
ASSISTANT_REJECT_RE = r"\bassistant\b"

# Big-tech special case: at these orgs "Program Manager" almost always
# means TPM/supply-chain; require a mission keyword in the TITLE itself.
MISSION_TITLE_KEYWORDS: tuple[str, ...] = (
    "social impact", "education", "community", "philanthropy",
    "citizenship", "sustainability", "inclusion", "nonprofit", ".org",
)

# Title clusters (scoring §3). Order matters: first match wins, D before
# B so "AI Policy Program Manager" lands in D not B.
CLUSTER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("D", r"responsible ai|ai governance|ai policy|model evaluation"
          r"|trust & safety|trust and safety"),
    ("C", r"workforce|employment|career (services|pathways)"
          r"|economic mobility|job placement|employer engagement"
          r"|education|school|district|teacher|curriculum|instructional"
          r"|learning|training|educator|mentorship|talent program"
          r"|early career|emerging talent|university relations"
          r"|fellowship|cohort|accessibility|disability|inclusion"
          r"|belonging"),
    ("B", r"operations|chief of staff|impact|social impact|community"
          r"|csr|corporate social responsibility|citizenship"
          r"|social innovation|sustainability|grants|compliance"
          r"|partnership"),
    ("E", r"director of|consultant|engagement manager"),
    ("A", r"program|project|initiative|portfolio"),
)

# Mission-fit keyword buckets (scoring §4), scanned over title + JD.
MISSION_CORE = ("education", "refugee", "immigrant", "disability",
                "workforce", "economic mobility", "asylee")
MISSION_ADJACENT = ("youth", "community development", "health equity",
                    "climate", "community programs", "first-generation")
MISSION_GENERIC = ("nonprofit", "mission", "philanthropy", "social impact",
                   "underserved", "equity")

COMP_FLOOR = 90_000
COMP_STRONG = 110_000

DEFAULT_FILTER_CONFIG: dict[str, Any] = {
    "bay_area_tokens": list(BAY_AREA_TOKENS),
    "reject_city_tokens": list(REJECT_CITY_TOKENS),
    "ambiguous_city_tokens": list(AMBIGUOUS_CITY_TOKENS),
    "comp_floor": COMP_FLOOR,
    "big_tech_orgs": [],
    # Placeholder registry — real one is vault/benji/filter_config.json.
    # org_type ∈ foundation|tech_csr|nonprofit|edtech|tech_general.
    "sources": [
        {"org": "ExampleFoundation", "platform": "greenhouse",
         "token": "examplefoundation", "org_type": "foundation",
         "tier": 1, "dream": False},
        {"org": "ExampleImpactOrg", "platform": "lever",
         "token": "exampleimpactorg", "org_type": "nonprofit",
         "tier": 1, "dream": False},
    ],
    "npag_enabled": False,      # real config turns this on (Tara #2)
    "dream_orgs": [],
    "_note": ("PII-free engine defaults. The real registry, dream list "
              "and tuning live in vault/benji/filter_config.json "
              "(gitignored), pointed to by BENJI_FILTER_CONFIG."),
}

# ─────────────────────────── Preferences (Tara #6) ────────────────────
# Generation-Spec §7 defaults, externalized. The vault file named by
# BENJI_PREFERENCES overrides; malformed values fall back per-key with a
# warning the digest surfaces (never a crash).
DEFAULT_PREFERENCES: dict[str, Any] = {
    "cover_letter_words": [350, 450],
    "morning_package_cap": 5,
    "cold_start_lookback_days": 14,
    "cold_start_digest_cap": 30,
    "weekly_rejects_sample": 20,
    "apply_threshold": 75,
    "career_break_mention": "only_if_posting_asks",
    "warm_contacts": "flag_in_review_only",
    "salary_expectations_field": "leave_blank_and_flag",
    "grantmaking_posting": "auto_drop_with_reason",
    "email_max_mb": 15,
}


def _load_json_file(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_filter_config() -> tuple[dict, list[str]]:
    """DEFAULTS overlaid with the vault file named by BENJI_FILTER_CONFIG.

    Returns (config, warnings). Warnings surface in the digest footer —
    a malformed vault file must be visible, not silent (PRD J5)."""
    cfg = json.loads(json.dumps(DEFAULT_FILTER_CONFIG))
    warnings: list[str] = []
    path = os.getenv("BENJI_FILTER_CONFIG")
    if os.getenv("RAHAT_TEST_MODE") == "1" and not path:
        return cfg, warnings          # hermetic: never read live vault
    data = _load_json_file(path)
    if path and data is None:
        warnings.append(f"filter config unreadable: {path} — using "
                        "engine defaults")
    elif data:
        cfg.update(data)
    return cfg, warnings


def load_preferences() -> tuple[dict, list[str]]:
    prefs = dict(DEFAULT_PREFERENCES)
    warnings: list[str] = []
    path = os.getenv("BENJI_PREFERENCES")
    if os.getenv("RAHAT_TEST_MODE") == "1" and not path:
        return prefs, warnings
    data = _load_json_file(path)
    if path and data is None:
        warnings.append(f"preferences unreadable: {path} — using defaults")
    elif data:
        for k, v in data.items():
            if k in prefs and isinstance(v, type(prefs[k])):
                prefs[k] = v
            elif k in prefs:
                warnings.append(f"preference {k} has wrong type — "
                                f"default kept")
            else:
                prefs[k] = v
    return prefs, warnings


# PII-free placeholder candidate source: enough vocabulary for the
# engine to run and tests to be meaningful. The real CLEAN v3 lives at
# vault/benji/resume_source_clean_v3.md (BENJI_CANDIDATE_SOURCE).
DEFAULT_CANDIDATE_SOURCE = """
Program and operations placeholder record. Program management,
partnership development, stakeholder engagement, grants management and
compliance on the grantee side, program design and measurement,
monitoring and evaluation, training design and facilitation, curriculum
development, team leadership, budget management, case management,
workforce development, economic mobility, education programs, funder
reporting, community engagement, service plans, retention.
"""


def load_candidate_source() -> tuple[str, list[str]]:
    path = os.getenv("BENJI_CANDIDATE_SOURCE")
    if os.getenv("RAHAT_TEST_MODE") == "1" and not path:
        return DEFAULT_CANDIDATE_SOURCE, []
    if path:
        try:
            with open(path) as f:
                return f.read(), []
        except Exception:
            return DEFAULT_CANDIDATE_SOURCE, [
                f"candidate source unreadable: {path} — placeholder in "
                "use; scores are NOT meaningful until this is fixed"]
    return DEFAULT_CANDIDATE_SOURCE, [
        "BENJI_CANDIDATE_SOURCE unset — placeholder vocabulary in use"]
