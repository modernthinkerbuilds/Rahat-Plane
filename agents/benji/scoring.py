"""benji.scoring — Scoring Rules v2, fully deterministic. No LLM.

100 points, weighted in the order she ranked them: experience match 40 ·
org type 25 · title cluster 20 · mission fit 10 · salary 5 · dream-org
bonus +10. The score decides how much gets WRITTEN, never what she sees.

Two rules with teeth:

* Tech CSR and foundations are DELIBERATELY TIED at 25 — two equal
  goals, not a first choice and a fallback. The Filter Config's
  mission-word-in-title gate is what keeps the tie honest at big tech.

* The dream-org bonus is the ONLY way a stretch role enters the queue:
  a reach role (Cluster E / senior-flag) at a dream org is shown and
  labeled `stretch`; a reach role anywhere else is dropped, with reason.

PRD precedence carve-out (v1.1, Tara #3): a 75+ role with coverage
below the 60% floor is labeled `stretch — low match` and no package is
ever built for it — the floor beats the band, always.
"""
from __future__ import annotations

import re
from datetime import datetime

from agents.benji.coverage import coverage as _coverage
from agents.benji.protocols import (
    BAND_APPLY,
    COVERAGE_FLOOR,
    MISSION_ADJACENT,
    MISSION_CORE,
    MISSION_GENERIC,
    ScoreResult,
    band_for,
)
from agents.benji.filtering import parse_comp

ORG_TYPE_POINTS = {
    "foundation": 25,
    "tech_csr": 25,       # deliberately tied — see module doc
    "nonprofit": 22,
    "edtech": 18,
    "tech_general": 8,
}

CLUSTER_POINTS = {"A": 20, "C": 18, "B": 16, "D": 14, "E": 8}

_FIFTEEN_PLUS = re.compile(r"\b1[5-9]\+?\s*(?:or more\s*)?years", re.I)


def experience_match_points(cov: float) -> int:
    pct = cov * 100
    if pct >= 80:
        return 40
    if pct >= 75:
        return 35
    if pct >= 70:
        return 30
    if pct >= 65:
        return 24
    if pct >= 60:
        return 18
    return 8


def mission_fit_points(title: str, jd_text: str) -> int:
    blob = f"{title} {jd_text}".lower()
    if any(k in blob for k in MISSION_CORE):
        return 10
    if any(k in blob for k in MISSION_ADJACENT):
        return 6
    if any(k in blob for k in MISSION_GENERIC):
        return 3
    return 0


def salary_points(comp_range: str, jd_text: str) -> int:
    comp = parse_comp(comp_range, jd_text)
    if comp is None:
        return 2               # unlisted: weak signal, not a reject
    if max(comp) >= 110_000:
        return 5
    if max(comp) >= 90_000:
        return 3
    return 0


def title_adjustments(title: str, jd_text: str, comp_range: str) -> int:
    adj = 0
    if _FIFTEEN_PLUS.search(jd_text or ""):
        adj -= 5               # asks 15+ years / clearly above the band
    t = (title or "").lower()
    comp = parse_comp(comp_range, jd_text)
    if "coordinator" in t and comp is not None and max(comp) < 100_000:
        adj -= 5               # step down at entry pay
    return adj


def score_job(posting: dict, cfg: dict, candidate_text: str,
              *, now: datetime | None = None) -> ScoreResult:
    """Deterministic score for one filter-passing posting."""
    title = posting.get("title", "")
    jd = posting.get("jd_text", "")
    org = posting.get("org", "")
    cluster = posting.get("title_cluster") or ""

    rep = _coverage(jd, candidate_text, job_title=title)
    exp = experience_match_points(rep.coverage)
    org_type = ""
    for s in cfg.get("sources", []):
        if s.get("org") == org:
            org_type = s.get("org_type", "")
            break
    org_pts = ORG_TYPE_POINTS.get(org_type, 8)
    cluster_pts = CLUSTER_POINTS.get(cluster, 8)
    cluster_pts += title_adjustments(title, jd,
                                     posting.get("comp_range", ""))
    mission = mission_fit_points(title, jd)
    salary = salary_points(posting.get("comp_range", ""), jd)

    dream = org in set(cfg.get("dream_orgs", []))
    bonus = 10 if dream else 0
    total = max(0, exp + org_pts + max(0, cluster_pts) + mission
                + salary + bonus)

    reach = cluster == "E" or "director of" in title.lower()
    stretch = False
    stretch_label = ""
    if reach:
        if dream:
            stretch, stretch_label = True, "stretch"
        # non-dream reach: pipeline drops it (drop_reach_outside_dream)
    if band_for(total) == BAND_APPLY and rep.coverage < COVERAGE_FLOOR:
        stretch, stretch_label = True, "stretch — low match"

    rationale_bits = [f"{int(rep.coverage * 100)}% match"]
    if org_type:
        rationale_bits.append(org_type.replace("_", " "))
    if dream:
        rationale_bits.append("dream org")
    breakdown = {"experience": exp, "org_type": org_pts,
                 "cluster": max(0, cluster_pts), "mission": mission,
                 "salary": salary, "dream_bonus": bonus}
    return ScoreResult(total=total, breakdown=breakdown,
                       band=band_for(total), stretch=stretch,
                       stretch_label=stretch_label,
                       rationale=" · ".join(rationale_bits),
                       coverage=rep.coverage,
                       unmatched=rep.unmatched)


def drop_reach_outside_dream(posting: dict, cfg: dict) -> str | None:
    """Scoring Rules: 'A reach role anywhere else is dropped.' Returns a
    reject reason, or None to keep."""
    cluster = posting.get("title_cluster") or ""
    title = (posting.get("title") or "").lower()
    reach = cluster == "E" or "director of" in title
    if reach and posting.get("org") not in set(cfg.get("dream_orgs", [])):
        return "reach role outside the dream-org list"
    return None


def sort_key(row: dict, *, now: datetime):
    """Tiebreakers, hers in order: fresh (≤7d) first, search-firm source
    first, smaller org first, >30d-old last — after score desc."""
    posted = row.get("posted_date")
    days = 9999
    if posted:
        try:
            days = (now.date()
                    - datetime.strptime(posted, "%Y-%m-%d").date()).days
        except ValueError:
            pass
    return (-(row.get("score") or 0),
            0 if days <= 7 else 1,
            0 if (row.get("source_tier") or 1) == 3 else 1,
            1 if days > 30 else 0,
            row.get("id") or 0)
