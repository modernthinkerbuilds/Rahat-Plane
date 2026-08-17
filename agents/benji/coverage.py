"""benji.coverage — THE weighted keyword-coverage function. Single brain.

Generation Spec §3, implemented once: scoring (experience match, 40
points) and S2 generation (the 75% floor, the unmatched-keyword list)
both import THIS module. The house single-brain rule, applied to
vocabulary instead of intents: if scorer and generator computed
coverage separately, the score that said 84% and the review.md that
says 71% would drift apart and she'd stop trusting both.

Weights (hers): job title 3.0 · required/minimum qualifications 2.5 ·
responsibilities 2.0 · preferred/nice-to-have 1.0 · about-the-org/EEO/
benefits boilerplate discarded.

Normalization (hers): lowercase, lemmatize (suffix-light, stdlib only),
treat multi-word phrases as single units, expand acronyms in BOTH
directions — a resume saying "monitoring and evaluation" must match a
JD saying "M&E".

The rule that matters most (hers, verbatim in spirit): coverage is
raised by selecting and reframing true bullets. It is NEVER raised by
adding a claim. This module only measures; it cannot inflate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SECTION_WEIGHTS: tuple[tuple[str, float], ...] = (
    (r"(required|minimum)\s+qualifications?|requirements|"
     r"what\s+you.ll\s+bring|who\s+you\s+are", 2.5),
    (r"responsibilit|what\s+you.ll\s+do|the\s+role|role\s+overview|"
     r"about\s+the\s+role|in\s+this\s+role", 2.0),
    (r"preferred|nice.to.have|bonus|great\s+if", 1.0),
    (r"about\s+(us|the\s+(org|organization|company|team|foundation))|"
     r"equal\s+opportunity|eeo|benefits|compensation|why\s+join|"
     r"our\s+values", 0.0),
)
TITLE_WEIGHT = 3.0
FLAT_WEIGHT = 2.0          # whole-JD fallback when no sections parse

# Both directions (hers): each pair normalizes to the same canonical
# token before matching.
ACRONYM_MAP: dict[str, str] = {
    "m&e": "monitoring and evaluation",
    "m and e": "monitoring and evaluation",
    "cbo": "community based organization",
    "cbos": "community based organization",
    "l&d": "learning and development",
    "l and d": "learning and development",
    "sow": "scope of work",
    "ta": "technical assistance",
    "csr": "corporate social responsibility",
    "dei": "diversity equity and inclusion",
    "deib": "diversity equity and inclusion",
    "pm": "program management",
    "k-12": "k12",
}

# Phrases treated as single units when present in a JD.
PHRASES: tuple[str, ...] = (
    "program management", "project management", "case management",
    "monitoring and evaluation", "learning and development",
    "grants management", "grant compliance", "funder reporting",
    "restricted funds", "budget management", "stakeholder engagement",
    "partnership development", "community engagement",
    "workforce development", "economic mobility", "curriculum development",
    "training design", "technical assistance", "capacity building",
    "program design", "impact measurement", "service plans",
    "systems navigation", "corporate social responsibility",
    "scope of work", "theory of change", "community based organization",
    "diversity equity and inclusion", "team leadership",
    "cross functional", "data analysis", "measurement architecture",
)

_STOP = frozenset("""a an and are as at be but by for from has have if in
into is it its of on or such that the their there these they this to was
will with we you your our who what when where how not can may more other
than then them us across within upon per each all any both about above
under over must able strong excellent years experience work working role
team including include includes ability skills required preferred plus
etc""".split())

_WORD = re.compile(r"[a-z0-9][a-z0-9&.-]*")


def _lemma(tok: str) -> str:
    """Suffix-light stemming — zero deps, deterministic, good enough for
    vocabulary matching (not linguistics)."""
    for suf in ("ings", "ing", "ments", "ment", "ations", "ation", "ies",
                "ers", "er", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            if suf == "ies":
                return tok[:-3] + "y"
            return tok[: len(tok) - len(suf)]
    return tok


def normalize(text: str) -> str:
    t = (text or "").lower()
    for k, v in ACRONYM_MAP.items():
        t = re.sub(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", v, t)
    return t


def _terms(text: str) -> set[str]:
    """Phrase units + lemmatized unigrams from normalized text."""
    t = normalize(text)
    found: set[str] = set()
    for ph in PHRASES:
        if ph in t:
            found.add(ph)
            t = t.replace(ph, " ")
    for w in _WORD.findall(t):
        w = w.strip(".-&")
        if len(w) >= 3 and w not in _STOP:
            found.add(_lemma(w))
    return found


@dataclass
class CoverageReport:
    coverage: float
    matched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)   # weight-ordered
    parse_quality: str = "sections"                       # or "flat"


def split_sections(jd_text: str) -> list[tuple[float, str]]:
    """Chunk the JD by heading-ish lines, weight each chunk."""
    lines = re.split(r"(?:\r?\n|(?<=[.:])\s{2,})", jd_text or "")
    chunks: list[tuple[float, list[str]]] = [(FLAT_WEIGHT, [])]
    seen_header = False
    for line in lines:
        probe = line.strip().lower()[:80]
        matched_w = None
        if probe and len(probe) < 80:
            for pat, w in SECTION_WEIGHTS:
                if re.search(pat, probe):
                    matched_w = w
                    break
        if matched_w is not None:
            seen_header = True
            chunks.append((matched_w, [line]))
        else:
            chunks[-1][1].append(line)
    if not seen_header:
        return [(FLAT_WEIGHT, jd_text or "")]
    return [(w, "\n".join(ls)) for w, ls in chunks if "\n".join(ls).strip()]


def coverage(jd_text: str, candidate_text: str, *,
             job_title: str = "") -> CoverageReport:
    cand = _terms(candidate_text)
    weighted: dict[str, float] = {}
    for term in _terms(job_title):
        weighted[term] = max(weighted.get(term, 0), TITLE_WEIGHT)
    sections = split_sections(jd_text)
    for w, chunk in sections:
        if w <= 0:
            continue
        for term in _terms(chunk):
            weighted[term] = max(weighted.get(term, 0), w)
    if not weighted:
        return CoverageReport(0.0, [], [], "flat")
    matched = {t for t in weighted if t in cand}
    total = sum(weighted.values())
    hit = sum(weighted[t] for t in matched)
    unmatched = sorted((t for t in weighted if t not in matched),
                       key=lambda t: (-weighted[t], t))
    quality = ("sections" if len(sections) > 1 else "flat")
    return CoverageReport(round(hit / total, 4),
                          sorted(matched), unmatched[:25], quality)
