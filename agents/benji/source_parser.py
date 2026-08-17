"""benji.source_parser — CLEAN v3 markdown → typed CandidateSource.

The vault source file (BENJI_CANDIDATE_SOURCE) is the ONLY verified
record (Generation Spec §0). This parser turns it into the typed model
the generator assembles from — so tailoring is literally selection over
parsed source bullets, and a claim that isn't in the parse cannot enter
a document through the assembly path at all. The LLM only ever touches
the profile paragraph and the letter, and both pass the verification
gate afterward.

Tolerant by design: a section the parser can't read degrades to absent
(and the generator's review.md says so) — never to invented content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Role:
    title: str
    org: str
    dates: str = ""
    location: str = ""
    context: str = ""              # italic org-description line
    bullets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # italic constraints
    max_bullets: int = 5
    default_pick: list[int] = field(default_factory=list)  # indexes


@dataclass
class Story:
    name: str
    detail: str
    use_for: str


@dataclass
class CandidateSource:
    identity_lines: list[str] = field(default_factory=list)
    experience_years: str = ""
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    profile: str = ""
    narrative: str = ""
    roles: list[Role] = field(default_factory=list)
    volunteer: list[str] = field(default_factory=list)
    skills_core: list[str] = field(default_factory=list)
    skills_situational: list[str] = field(default_factory=list)
    skills_technical: list[str] = field(default_factory=list)
    stories: list[Story] = field(default_factory=list)
    usable_lines: list[str] = field(default_factory=list)
    never_claim: list[str] = field(default_factory=list)
    never_write: list[str] = field(default_factory=list)
    raw_text: str = ""
    # raw_text MINUS the DO-NOT-USE section. The number-tracing gate
    # must check against THIS: the DO-NOT-USE list literally contains
    # the forbidden figures ("$750K (it was $500K)"), so tracing against
    # raw_text would bless exactly the numbers it bans — caught by the
    # S2 pins before it could ship.
    sanctioned_text: str = ""


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MAX4 = re.compile(r"(four|4)\s+bullets?\s+maximum", re.I)


def _strip_md(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    return s.strip()


def _split_dotted(line: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s·\s|\s+·\s+", line)
            if p.strip()]


def parse_source(text: str) -> CandidateSource:
    src = CandidateSource(raw_text=text)
    lines = text.splitlines()
    section = ""
    sub = ""
    role: Role | None = None
    sanctioned: list[str] = []

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        m = re.match(r"^##\s+(.*)", line)
        if m and not line.startswith("###"):
            section = m.group(1).strip().upper()
            sub, role = "", None
            continue

        if not section.startswith("DO-NOT-USE"):
            sanctioned.append(line)

        if section.startswith("EXPERIENCE"):
            m = re.match(r"^###\s+(.*)", line)
            if m:
                head = _strip_md(m.group(1))
                if " — " in head:
                    title, org = head.split(" — ", 1)
                else:
                    title, org = head, ""
                role = Role(title=title.strip(), org=org.strip())
                src.roles.append(role)
                continue
            if role is None:
                continue
            mb = re.match(r"^\*\*(.+?)\*\*\s*$", stripped)
            if mb and not role.dates:
                parts = _split_dotted(mb.group(1))
                role.dates = parts[0] if parts else mb.group(1)
                role.location = parts[1] if len(parts) > 1 else ""
                continue
            if stripped.startswith("- "):
                role.bullets.append(_strip_md(stripped[2:]))
                continue
            if (stripped.startswith("*") and stripped.endswith("*")
                    and not stripped.startswith("**")):
                note = _strip_md(stripped)
                role.notes.append(note)
                if _MAX4.search(note):
                    role.max_bullets = 4
                continue
            if stripped.startswith("**Five-bullet version"):
                # "funding doubling · 1,200+ households · …" — map each
                # descriptor to the bullet sharing the most of its words
                # (word order in the descriptor differs from the bullet).
                desc = _strip_md(stripped.split(":", 1)[-1])

                def _words(s: str) -> set[str]:
                    return {w for w in re.findall(
                        r"[a-z0-9$+]+", s.lower().replace(",", ""))
                        if len(w) > 2 or any(c.isdigit() for c in w)}

                for want in _split_dotted(desc):
                    ww = _words(want)
                    if not ww:
                        continue
                    best, best_frac = None, 0.0
                    for i, b in enumerate(role.bullets):
                        if i in role.default_pick:
                            continue
                        frac = len(ww & _words(b)) / len(ww)
                        if frac > best_frac:
                            best, best_frac = i, frac
                    if best is not None and best_frac >= 0.5:
                        role.default_pick.append(best)
                continue
            if stripped and not stripped.startswith(("**", "#")) \
                    and not role.bullets and not role.context:
                if not stripped.startswith("*"):
                    role.context = _strip_md(stripped)
            continue

        if section.startswith("IDENTITY"):
            if (stripped.startswith("*") and stripped.endswith("*")
                    and not stripped.startswith("**")):
                continue    # italic INSTRUCTION line ("Always attach…"),
                            # never resume content — the gate caught one
                            # rendering as a certification (2026-08-17)
            if stripped.startswith("**Experience:**"):
                src.experience_years = _strip_md(
                    stripped.split(":", 1)[1])
            elif stripped.startswith("**Education"):
                sub = "edu"
            elif stripped.startswith("**Certifications"):
                sub = "cert"
            elif stripped.startswith("**") and stripped.endswith("**"):
                src.identity_lines.append(_strip_md(stripped))
                sub = "id"
            elif stripped and not stripped.startswith("#"):
                t = _strip_md(stripped)
                if sub == "edu":
                    src.education.append(t)
                elif sub == "cert":
                    src.certifications.append(t)
                elif t:
                    src.identity_lines.append(t)
            continue

        if section.startswith("POSITIONING"):
            if stripped.startswith(">"):
                src.profile = (src.profile + " "
                               + _strip_md(stripped.lstrip("> "))).strip()
            elif stripped.startswith("**Career narrative"):
                sub = "narr"
            elif stripped.startswith("**Through-line"):
                sub = "thru"
            elif stripped and sub == "narr" and not stripped.startswith(
                    ("**", "#")):
                src.narrative = (src.narrative + " "
                                 + _strip_md(stripped)).strip()
            continue

        if section.startswith("VOLUNTEER"):
            if stripped and not stripped.startswith("#"):
                src.volunteer.append(_strip_md(stripped))
            continue

        if section.startswith("SKILLS"):
            for label, dest in (("**Core:**", src.skills_core),
                                ("**Situational:**",
                                 src.skills_situational),
                                ("**Technical:**", src.skills_technical)):
                if stripped.startswith(label):
                    dest.extend(_split_dotted(
                        _strip_md(stripped[len(label):])))
            continue

        if section.startswith("VOICE"):
            if re.match(r'^-\s*"', stripped):
                src.usable_lines.append(
                    stripped.lstrip("- ").strip().strip('"'))
            continue

        if section.startswith("STORY BANK"):
            m = re.match(r"^\|\s*\*\*(.+?)\*\*\s*(?:—\s*)?(.*?)\s*\|"
                         r"\s*(.*?)\s*\|", stripped)
            if m:
                src.stories.append(Story(name=m.group(1).strip(),
                                         detail=_strip_md(m.group(2)),
                                         use_for=_strip_md(m.group(3))))
            continue

        if section.startswith("DO-NOT-USE"):
            if stripped.startswith("**Never claim"):
                sub = "claim"
            elif stripped.startswith("**Never write"):
                sub = "write"
            elif stripped.startswith("- "):
                item = _strip_md(stripped[2:])
                (src.never_claim if sub == "claim"
                 else src.never_write).append(item)
            continue

    src.sanctioned_text = "\n".join(sanctioned)
    return src
