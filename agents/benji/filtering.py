"""benji.filtering — Filter Config v1 mechanics, deterministic.

Design principle carried over verbatim: **bias toward FLAG over
REJECT.** A false reject is invisible — she never learns about the role
she didn't see. A false flag costs ten seconds in the morning queue.
Every reject keeps its reason (Tara #4); the Sunday sample is how the
title pattern gets calibrated.

Order (cheapest kill first, per the config):
    1. hard location/work-mode filter
    2. compensation floor (posted ranges only; unlisted → weak signal,
       score normally)
    3. title structural match (≥1 NOUN and ≥1 LEVEL) → exclude list →
       level guard
    4. big-tech mission-word-in-title gate
    5. cluster classification (feeds scoring §3 — a boost, not a gate)
"""
from __future__ import annotations

import re

from agents.benji.protocols import (
    AMBIGUOUS_CITY_TOKENS,
    ASSISTANT_REJECT_RE,
    CLUSTER_PATTERNS,
    FilterOutcome,
    LEVEL_REJECT_TOKENS,
    MISSION_TITLE_KEYWORDS,
    TITLE_EXCLUDE_TOKENS,
    TITLE_LEVEL_RE,
    TITLE_NOUN_RE,
)

_NOUN = re.compile(TITLE_NOUN_RE, re.I)
_LEVEL = re.compile(TITLE_LEVEL_RE, re.I)
_ASSISTANT = re.compile(ASSISTANT_REJECT_RE, re.I)
_CLUSTERS = tuple((c, re.compile(p, re.I)) for c, p in CLUSTER_PATTERNS)

_COMP_RANGE = re.compile(
    r"\$\s?(\d{2,3})(?:[,.]?(\d{3}))?\s?([kK])?\s?(?:-|–|—|to)\s?"
    r"\$?\s?(\d{2,3})(?:[,.]?(\d{3}))?\s?([kK])?")


def infer_work_mode(location: str, jd_text: str = "") -> str:
    blob = f"{location} {jd_text[:400]}".lower()
    if "hybrid" in blob:
        return "hybrid"
    if "remote" in blob:
        return "remote"
    if "on-site" in blob or "onsite" in blob or "in-office" in blob:
        return "onsite"
    return ""


def parse_comp(comp_range: str, jd_text: str = "") -> tuple[int, int] | None:
    """Best-effort (lo, hi) in dollars from the posted range or JD."""
    for blob in (comp_range or "", jd_text or ""):
        m = _COMP_RANGE.search(blob)
        if m:
            lo = int(m.group(1)) * (1000 if m.group(3) else 1)
            if m.group(2):
                lo = int(m.group(1) + m.group(2))
            hi = int(m.group(4)) * (1000 if m.group(6) else 1)
            if m.group(5):
                hi = int(m.group(4) + m.group(5))
            if lo < 1000:
                lo *= 1000
            if hi < 1000:
                hi *= 1000
            return (lo, hi)
    return None


def _has_bay_token(loc: str, cfg: dict) -> bool:
    return any(t in loc for t in cfg["bay_area_tokens"])


def _has_reject_city(loc: str, cfg: dict) -> bool:
    return any(t in loc for t in cfg["reject_city_tokens"])


def _bare_ambiguous(loc: str, cfg: dict) -> bool:
    for t in cfg.get("ambiguous_city_tokens", AMBIGUOUS_CITY_TOKENS):
        if t in loc and f"{t}, ca" not in loc and f"{t} ca" not in loc:
            return True
    return False


def hard_location_filter(location: str, work_mode: str,
                         jd_text: str, cfg: dict) -> FilterOutcome:
    loc = (location or "").lower()
    blob = f"{loc} {(jd_text or '')[:600].lower()}"

    if "relocation" in blob and ("required" in blob or "assistance" in blob):
        return FilterOutcome("reject", "relocation required/expected")

    if work_mode == "remote":
        if re.search(r"remote[^.]{0,40}(emea|apac|india|canada|uk\b)", blob):
            return FilterOutcome("reject", "remote restricted to another "
                                           "country/region")
        m = re.search(r"(?:remote[^.]{0,80}?|eligible states?[^.]{0,20}?)"
                      r"\b(ny|tx|ma|dc|wa|co|new york|texas|massachusetts|"
                      r"washington|colorado)\b[^.]{0,120}", blob)
        if m and not re.search(r"\b(ca\b|california)", m.group(0)):
            return FilterOutcome("reject", "remote restricted to states "
                                           "not including CA")
        return FilterOutcome("accept", "remote US")

    if work_mode in ("hybrid", "onsite"):
        if _has_bay_token(loc, cfg):
            return FilterOutcome("accept", f"{work_mode} in Bay Area")
        if _bare_ambiguous(loc, cfg):
            return FilterOutcome("flag", "ambiguous city token — needs "
                                         "state (Newark/Richmond/"
                                         "Washington rule)")
        if _has_reject_city(loc, cfg):
            return FilterOutcome("reject", f"{work_mode} anchored to "
                                           "non-Bay-Area city")
        if work_mode == "hybrid" and not loc:
            return FilterOutcome("flag", "'Hybrid' with no office named")
        if loc:
            # Multi-location: any office in the Bay accepts.
            return FilterOutcome("flag", "location unrecognized — "
                                         "morning-queue check")
        return FilterOutcome("flag", "no location stated")

    # work_mode unknown
    if not loc:
        return FilterOutcome("flag", "no work mode and no location stated")
    if _has_bay_token(loc, cfg):
        return FilterOutcome("accept", "Bay Area location, mode unstated")
    if loc in ("united states", "usa", "us"):
        return FilterOutcome("flag", "country-only location, nothing finer")
    if _bare_ambiguous(loc, cfg):
        return FilterOutcome("flag", "ambiguous city token — needs state")
    if _has_reject_city(loc, cfg):
        return FilterOutcome("reject", "anchored to non-Bay-Area city")
    return FilterOutcome("flag", "work_mode absent, location is just a "
                                 "city name")


def title_filter(title: str, cfg: dict) -> FilterOutcome:
    t = (title or "").lower()
    for tok in TITLE_EXCLUDE_TOKENS:
        if tok in t:
            return FilterOutcome("reject", f"excluded title token: {tok}")
    for tok in LEVEL_REJECT_TOKENS:
        if tok in t:
            return FilterOutcome("reject", f"level guard: {tok.strip()}")
    if _ASSISTANT.search(t):
        return FilterOutcome("reject", "level guard: assistant is a step "
                                       "down from associate")
    if not (_NOUN.search(t) and _LEVEL.search(t)):
        return FilterOutcome("reject", "title lacks NOUN+LEVEL structural "
                                       "match")
    return FilterOutcome("accept", "title matches pattern")


def big_tech_gate(org: str, title: str, cfg: dict) -> FilterOutcome:
    if org not in set(cfg.get("big_tech_orgs", [])):
        return FilterOutcome("accept", "")
    t = title.lower()
    if any(k in t for k in MISSION_TITLE_KEYWORDS):
        return FilterOutcome("accept", "big-tech title carries mission "
                                       "keyword")
    return FilterOutcome("reject", "big-tech org without mission keyword "
                                   "in the title itself (80%-volume/"
                                   "2%-value rule)")


def classify_cluster(title: str) -> str:
    t = (title or "").lower()
    for cluster, pat in _CLUSTERS:
        if pat.search(t):
            return cluster
    return ""


def apply_filters(posting: dict, cfg: dict) -> FilterOutcome:
    """Run the full Filter Config cascade over one normalized posting."""
    work_mode = posting.get("work_mode") or infer_work_mode(
        posting.get("location", ""), posting.get("jd_text", ""))
    posting["work_mode"] = work_mode

    comp = parse_comp(posting.get("comp_range", ""),
                      posting.get("jd_text", ""))
    if comp is None:
        posting["comp_unlisted"] = True
    elif max(comp) < cfg.get("comp_floor", 90_000):
        return FilterOutcome("reject",
                             f"comp below floor (${max(comp):,} < "
                             f"${cfg.get('comp_floor', 90_000):,})")

    loc = hard_location_filter(posting.get("location", ""), work_mode,
                               posting.get("jd_text", ""), cfg)
    if loc.result == "reject":
        return loc

    t = title_filter(posting.get("title", ""), cfg)
    if t.result == "reject":
        return t

    g = big_tech_gate(posting.get("org", ""), posting.get("title", ""), cfg)
    if g.result == "reject":
        return g

    cluster = classify_cluster(posting.get("title", ""))
    flags = [loc.reason] if loc.result == "flag" else []
    return FilterOutcome("flag" if loc.result == "flag" else "accept",
                         loc.reason if loc.result == "flag" else "passed",
                         cluster=cluster, flags=flags)
