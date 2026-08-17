"""benji.verification — the gate. Nothing reaches the review queue (or
an email) until it passes every check; a failure BLOCKS output, it does
not produce a warning the agent then ignores (Generation Spec §5).

All checks are pure functions over generated text + the parsed CLEAN v3
source. Mechanical on purpose: "five fabricated claims reached a
near-final resume" because prose from older documents was treated as
verified — a gate that itself used an LLM to judge would recreate that
class. This one greps.

Sovereignty (repo is PUBLIC): the fact-invariant RULES are data, not
code. The engine below knows three rule shapes; the real rules — which
necessarily name the candidate's employers — live in the vault file
named by BENJI_GATE_RULES (generated from CLEAN v3's DO-NOT-USE list).
The defaults in code are PII-free placeholders that exercise every rule
shape for the tests.

The adversarial property S1's design note promised: a JD (untrusted
input) that talks the LETTER model into a fabricated claim produces a
package that fails HERE — the gate doesn't know or care where the words
came from.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from agents.benji.source_parser import CandidateSource

# Style rules (generic, no personal facts) stay in code.
BANNED_PHRASES: tuple[str, ...] = (
    "i am thrilled to apply", "uniquely suited", "uniquely qualified",
    "ideal candidate", "fire-in-the-belly", "the beating heart of the",
    "80,000 hours", "4,400 stories",
    "complex landscape navigation is my superpower",
    "rock-solid program management abilities",
    "passionate about", "i was excited to see", "aligns perfectly with",
    "at the intersection of",
)

# Rule shapes the engine understands:
#   forbid        — regex anywhere in the doc → failure
#   forbid_resume — regex in the RESUME only → failure
#   claim_forbid  — regex only when claimed (ownership verbs within 60
#                   chars) → failure; a NEGATED mention passes
#   pair_line     — token present on a line WITHOUT its required
#                   companion on the same line → failure
DEFAULT_GATE_RULES: list[dict] = [
    {"kind": "forbid", "pattern": r"placeholder-forbidden-claim",
     "why": "placeholder forbid rule (real rules live in vault)"},
    {"kind": "forbid_resume", "pattern": r"\bplaceholder-resume-only\b",
     "why": "placeholder resume-only rule"},
    {"kind": "claim_forbid", "pattern": r"placeholder[\s-]?craft",
     "why": "placeholder claim-context rule"},
    {"kind": "pair_line", "token": "placeholderorg",
     "requires": "(volunteer)",
     "why": "placeholder pair rule"},
]

_CLAIM_VERBS = (r"(led|managed|owned|directed|drove|oversaw|"
                r"responsible for|experience (in|with)|expertise in|"
                r"skilled in|background in)")

_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s*[%kKmM+]*")
_YEAR = re.compile(r"^(19|20)\d{2}$")


def load_gate_rules() -> tuple[list[dict], list[str]]:
    path = os.getenv("BENJI_GATE_RULES")
    if not path:
        return list(DEFAULT_GATE_RULES), (
            [] if os.getenv("RAHAT_TEST_MODE") == "1" else
            ["BENJI_GATE_RULES unset — only placeholder fact-invariants "
             "active; the DO-NOT-USE gate is NOT protecting real facts"])
    try:
        with open(path) as f:
            rules = json.load(f)
        if isinstance(rules, dict):
            rules = rules.get("rules", [])
        return rules, []
    except Exception:
        return list(DEFAULT_GATE_RULES), [
            f"gate rules unreadable: {path} — placeholder rules only"]


def numeric_tokens(text: str) -> set[str]:
    """Normalized numeric claims: '$500K' ≡ '$500,000' ≡ '500,000';
    percents keep their sign; calendar years are excluded."""
    out: set[str] = set()
    for m in _NUM.finditer(text or ""):
        tok = m.group(0).strip()
        raw = tok.lstrip("$").rstrip("+").strip()
        pct = raw.endswith("%")
        raw = raw.rstrip("%").strip()
        mult = 1
        if raw and raw[-1] in "kK":
            raw, mult = raw[:-1], 1000
        elif raw and raw[-1] in "mM":
            raw, mult = raw[:-1], 1_000_000
        raw = raw.replace(",", "")
        if not raw or not raw.replace(".", "").isdigit():
            continue
        if _YEAR.match(raw) and mult == 1 and not pct:
            continue
        try:
            val = float(raw) * mult
        except ValueError:
            continue
        out.add(f"{val:g}" + ("%" if pct else ""))
    return out


@dataclass
class GateReport:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def check_numbers(doc: str, source: CandidateSource,
                  label: str) -> list[str]:
    # sanctioned_text, NOT raw_text: the DO-NOT-USE section contains the
    # forbidden figures, so tracing against the whole file would bless
    # exactly the numbers it bans (caught by the S2 pins, 2026-08-17).
    src_nums = numeric_tokens(source.sanctioned_text or source.raw_text)
    return [f"{label}: number not traceable to CLEAN v3: {tok}"
            for tok in sorted(numeric_tokens(doc))
            if tok not in src_nums]


def _apply_rule(rule: dict, doc: str, label: str,
                is_resume: bool) -> str | None:
    low = doc.lower()
    kind = rule.get("kind")
    why = rule.get("why", "rule violation")
    if kind == "forbid" and re.search(rule["pattern"], low):
        return f"{label}: {why}"
    if kind == "forbid_resume" and is_resume \
            and re.search(rule["pattern"], low):
        return f"{label}: {why}"
    if kind == "claim_forbid":
        # (?:...) wrap is load-bearing: a vault pattern with a top-level
        # alternation ("a|b") would otherwise contribute a BARE branch
        # that matches any mention — which blocked the Hewlett letter's
        # own "never sat on the grantmaking side" move (rehearsal,
        # 2026-08-17).
        pat = f"(?:{rule['pattern']})"
        claim = re.compile(_CLAIM_VERBS + r"[^.\n]{0,60}" + pat + "|"
                           + pat + r"[^.\n]{0,30}\b(experience|expertise"
                           r"|background|skills)\b", re.I)
        if is_resume and re.search(pat, low):
            return f"{label}: {why} (any mention, resume)"
        for m in claim.finditer(doc):
            pre = doc[max(0, m.start() - 40):m.start()].lower()
            if re.search(r"\b(never|not|no|without)\b[^.\n]{0,30}$", pre):
                continue          # negated — naming the gap passes
            return f"{label}: {why} (claimed)"
    if kind == "pair_line":
        tok, req = rule["token"].lower(), rule["requires"].lower()
        for ln in doc.splitlines():
            l = ln.lower()
            if tok in l and req not in l:
                return (f"{label}: {why}: '{ln.strip()[:70]}'")
    return None


def check_rules(doc: str, rules: list[dict], label: str, *,
                is_resume: bool) -> list[str]:
    fails = []
    for rule in rules:
        try:
            hit = _apply_rule(rule, doc, label, is_resume)
        except re.error:
            continue                   # malformed vault rule ≠ crash
        if hit:
            fails.append(hit)
    low = doc.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            fails.append(f"{label}: banned phrase: \"{phrase}\"")
    return fails


def check_resume_structure(role_bullets: dict[str, tuple[int, int]],
                           label: str = "resume") -> list[str]:
    return [f"{label}: {role}: {n} bullets exceeds cap of {cap}"
            for role, (n, cap) in role_bullets.items() if n > cap]


def verify_package(*, resume_text: str, letter_text: str,
                   source: CandidateSource,
                   role_bullets: dict[str, tuple[int, int]]) -> GateReport:
    """The full §5 gate over both rendered documents' text."""
    rules, rule_warnings = load_gate_rules()
    rep = GateReport(warnings=list(rule_warnings))
    rep.failures += check_numbers(resume_text, source, "resume")
    rep.failures += check_numbers(letter_text, source, "letter")
    rep.failures += check_rules(resume_text, rules, "resume",
                                is_resume=True)
    rep.failures += check_rules(letter_text, rules, "letter",
                                is_resume=False)
    rep.failures += check_resume_structure(role_bullets)
    wc = len(letter_text.split())
    if letter_text and not 250 <= wc <= 520:
        rep.warnings.append(f"letter length {wc} words (target 350–450)")
    return rep
