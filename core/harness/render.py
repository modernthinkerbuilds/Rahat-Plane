"""core.harness.render — the deterministic reply (ADR-018 §2.2 step 7).

Code writes the reply from the structured answer and the evidence
records: every number, time, address, status and rating comes from a
record; the model's own words appear only as the "why" line, the diner
lines, the flags and the direct answers, and those pass the banned
list. The frame's header is the first line so a wrong reading costs one
word to fix. One phone screen; a sources line; "Checked HH:MM".

Telegram legacy Markdown: names are escaped, links are `[text](url)`.
`render()` never raises; problems (a banned phrase, a pick without a
place) come back in `Rendered.problems` for the controller.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime

from core import hours as chours
from core.harness.answer import Answer, Pick
from core.harness.evidence import Evidence, PlaceEvidence, arrival_for
from core.harness.frame import Frame

MAX_CHARS = 1800
_MD_SPECIAL = "_*`["
_URL_ENC = {" ": "%20", "(": "%28", ")": "%29", "_": "%5F", "*": "%2A", "`": "%60", "[": "%5B", "]": "%5D"}


def md_escape(text: str) -> str:
    return "".join(("\\" + ch) if ch in _MD_SPECIAL else ch for ch in str(text or ""))


def md_url(url: str) -> str:
    return "".join(_URL_ENC.get(ch, ch) for ch in (url or "").strip())


@dataclass
class Rendered:
    text: str = ""
    sources: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)      # banned phrases, missing places
    disclosures: list[str] = field(default_factory=list)
    chars: int = 0
    trimmed: bool = False


def _banned(text: str, banned: list[str]) -> list[str]:
    low = (text or "").lower()
    return [b for b in banned if b and b.lower() in low]


def _fmt_time(dt: datetime) -> str:
    return chours.fmt(dt.hour * 60 + dt.minute)


def _rating_line(pe: PlaceEvidence) -> str | None:
    r = pe.rating
    if not r or r.get("value") is None:
        return None
    count = r.get("count")
    cnt = f" ({int(count):,})" if count else " (count unknown)"
    return f"{r.get('source') or 'rating'} {r['value']}{cnt}"


def _hours_phrase(pe: PlaceEvidence, when: datetime) -> tuple[str, bool]:
    """('open till 10 PM' | 'closes in 25 min' | 'closed Mon' | 'hours unconfirmed — call first', soft?)"""
    res = chours.open_at(pe.hours(), when.weekday(), when.hour * 60 + when.minute, last_seating_before_close_min=30)
    if res["open"] is None:
        return "hours unconfirmed — call first", True
    if res["open"] is False:
        return f"{res['why']} {when.strftime('%a')}", True
    if not res.get("last_seating_ok", True):
        return f"closes in {res['closes_in_min']} min ({res['closes_at']})", True
    return f"open till {res['closes_at']}", False


def _links(pe: PlaceEvidence) -> list[str]:
    p = pe.place or {}
    out = []
    map_url = pe.value("map_url")
    if not (isinstance(map_url, str) and map_url.startswith("http")):
        q = ", ".join(x for x in (p.get("name"), p.get("address") or p.get("neighborhood"), p.get("city")) if x)
        map_url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(q)
    out.append(f"[Map]({md_url(map_url)})")
    yelp = pe.value("yelp_url")
    if not (isinstance(yelp, str) and yelp.startswith("http")):
        yelp = "https://www.yelp.com/search?" + urllib.parse.urlencode(
            {"find_desc": p.get("name") or pe.place_id, "find_loc": (p.get("city") or "").replace("_", " ")})
    out.append(f"[Yelp]({md_url(yelp)})")
    return out


def _source_label(f: dict) -> str | None:
    url = f.get("source_url") or ""
    name = f.get("source_name") or ""
    date = str(f.get("observed_at") or "")[:10]
    host = urllib.parse.urlparse(url).netloc.replace("www.", "") if url.startswith("http") else ""
    label = host or (name if name and name not in ("model", "seed") else "") or ("household notes" if f.get("method") in ("seed", "owner", "user_report") else "")
    if not label:
        return None
    try:
        dt = datetime.strptime(date, "%Y-%m-%d") if date else None
        d = f"{dt.strftime('%b')} {dt.day}" if dt else ""
    except ValueError:
        d = date
    return f"{label} ({d})" if d else label


def render(frame: Frame, answer: Answer, evidence: Evidence, *, now: datetime, mode: str = "home",
           origin: tuple[float, float] | None = None, minutes_mode: str = "drive", banned: list[str] | None = None,
           chains_label_from: int = 2, disclosures: list[str] | None = None, header: str | None = None,
           max_chars: int = MAX_CHARS) -> Rendered:
    banned = banned or []
    out = Rendered(disclosures=list(disclosures or []))
    lines: list[str] = []
    sources: list[str] = []

    def take(text: str, where: str) -> str:
        hits = _banned(text, banned)
        if hits:
            out.problems.append(f"{where}: banned phrase {hits}")
            for h in hits:
                text = re.sub(re.escape(h), "—", text, flags=re.I)
        return text

    head = header if header is not None else frame.header()
    if head:
        lines.append(f"_{md_escape(head)}_")
    for ln in answer.lines:
        lines.append(md_escape(take(ln, "line")))
    if answer.assumptions:
        lines.append("Assuming: " + md_escape(take("; ".join(a.strip().rstrip(".") for a in answer.assumptions), "assumptions")) + ".")
    if answer.lifted_exclusions:
        lines.append(md_escape(f"({', '.join(answer.lifted_exclusions)} is usually off the list; on today because you asked.)"))

    picks = [p for p in answer.picks if p.role != "backup"] + [p for p in answer.picks if p.role == "backup"]
    multi = len(picks) >= 2
    rec = answer.recommended()
    for i, pick in enumerate(picks, 1):
        lines += _pick_block(pick, i if multi else None, frame, evidence, now=now, origin=origin, minutes_mode=minutes_mode,
                             take=take, sources=sources, chains_label_from=chains_label_from, out=out,
                             rec_id=rec.place_id if (rec and multi) else None)

    if answer.plan:
        lines.append("")
        for s in answer.plan:
            place = evidence.place(s.place_id) if s.place_id else None
            name = md_escape(place.name if place and place.exists else (s.name or ""))
            label = md_escape(take(s.label, 'slot'))
            seg = f"{md_escape(s.time)} — {label}" + (f": *{name}*" if name and name != label else "")
            if s.about_minutes_from_previous:
                seg += f" (about {s.about_minutes_from_previous} min)"
            if s.note:
                seg += f" — {md_escape(take(s.note, 'slot note'))}"
            lines.append(seg)
            if place and place.exists:
                for f in place.facts.values():
                    lab = _source_label(f)
                    if lab and lab not in sources:
                        sources.append(lab)

    if multi and rec:
        why = take(rec.why or "", "why")
        lines.append(f"My pick: *{md_escape(rec.name)}*" + (f" — {md_escape(why)}" if why else ""))
    if answer.none_claim:
        nc = answer.none_claim
        near = ""
        if nc.nearest_place_id:
            pe = evidence.place(nc.nearest_place_id)
            near = f" Nearest that fits: {md_escape(pe.name)}."
        elif nc.nearest_name:
            near = f" Nearest that fits: {md_escape(nc.nearest_name)}."
        lines.append(md_escape(f"Nothing fits in {nc.area} at {nc.hours} — checked {', '.join(nc.searched) or 'the knowledge base'}.") + near)
    if answer.left_out:
        parts = [f"{md_escape(lo.name)} ({md_escape(take(lo.reason, 'left out'))})" for lo in answer.left_out[:4]]
        lines.append("Left out: " + " · ".join(parts) + ".")
    if answer.question:
        lines.append("Q: " + md_escape(take(answer.question, "question")))
    for d in out.disclosures:
        lines.append(md_escape(d))
    if sources:
        out.sources = sources
    tail = ("Sources: " + md_escape(", ".join(sources[:6])) + " · " if sources else "") + f"Checked {_fmt_time(now)}"
    lines.append(tail)

    text = "\n".join(lines)
    if len(text) > max_chars:
        out.trimmed = True
        text = _trim(lines, max_chars)
    out.text = text
    out.chars = len(text)
    return out


def _pick_block(pick: Pick, idx: int | None, frame: Frame, evidence: Evidence, *, now: datetime,
                origin: tuple[float, float] | None, minutes_mode: str, take, sources: list[str],
                chains_label_from: int, out: Rendered, rec_id: str | None = None) -> list[str]:
    pe = evidence.place(pick.place_id)
    if not pe.exists:
        out.problems.append(f"pick {pick.name!r} ({pick.place_id}) is not in the knowledge base")
        return []
    p = pe.place or {}
    bits: list[str] = []
    cuisine = (p.get("cuisine") or "").replace("_", " ")
    if cuisine:
        bits.append(cuisine)
    if pe.price_level:
        bits.append(pe.price_level)
    else:
        bits.append("price unknown")
    r = _rating_line(pe)
    bits.append(r if r else "no counted rating")
    minutes = evidence.minutes(origin, pick.place_id, minutes_mode)
    when, _ = arrival_for(frame, now, about_minutes=minutes if minutes is not None else pick.about_minutes,
                          arrival_hint=pick.arrival)
    hrs, soft = _hours_phrase(pe, when)
    bits.append(hrs)
    if minutes is not None:
        bits.append(f"about {minutes} min {'walk' if minutes_mode == 'walk' else minutes_mode}")
    n = pe.location_count
    if n is not None and n >= chains_label_from:
        bits.append(f"{n} locations")
    label = f"{idx}. " if idx else ""
    branch = f" [{md_escape(pick.branch)}]" if pick.branch else ""
    role = " (backup)" if pick.role == "backup" else (" (split order)" if pick.role == "split" else "")
    lines = [f"{label}*{md_escape(pe.name)}*{branch}{role} — {md_escape(' · '.join(bits))}"]
    where = p.get("neighborhood") or p.get("address")
    if where:
        lines[0] += f" · {md_escape(where)}"
    why = take(pick.why or "", "why")
    if why and not (idx is not None and pick.place_id == rec_id):   # the pick's why goes on the "My pick" line
        lines.append(f"   {md_escape(why)}")
    for d in pick.diner_lines:
        tag = "" if d.verified else " (unverified)" if pe.menu else ""
        note = f" — {md_escape(take(d.note, 'note'))}" if d.note else ""
        lines.append(f"   {md_escape(d.member)}: {md_escape(take(d.dish, 'dish'))}{note}{tag}")
    for fl in pick.flags[:3]:
        lines.append(f"   ⚑ {md_escape(take(fl.text, 'flag'))}")
    if pick.next_step:
        lines.append(f"   → {md_escape(take(pick.next_step, 'next step'))}")
    acclaim = pe.acclaim[0] if pe.acclaim else None
    if acclaim:
        who = acclaim.get("writer") or "a critic's list"
        lines.append(f"   ✎ {md_escape(who)}" + (f", {md_escape(str(acclaim['date'])[:4])}" if acclaim.get("date") else ""))
    lines.append("   " + " · ".join(_links(pe)))
    for f in pe.facts.values():
        lab = _source_label(f)
        if lab and lab not in sources:
            sources.append(lab)
    return lines


def _trim(lines: list[str], max_chars: int) -> str:
    """Progressively drop the least important lines until the text fits:
    acclaim, then flags, then the left-out line, then diner notes, then
    the why lines; the header and the sources/checked tail always stay."""
    kept = list(lines)
    tail = kept.pop() if kept else ""

    def fits() -> bool:
        return len("\n".join(kept + [tail])) <= max_chars

    for pattern in (r"^\s+✎ ", r"^\s+⚑ ", r"^Left out: "):
        if fits():
            break
        kept = [ln for ln in kept if not re.match(pattern, ln)]
    if not fits():   # diner notes: "   member: dish — note" → "   member: dish"
        kept = [re.sub(r"^(\s+[^:\n]+: [^—\n]+) — .*$", r"\1", ln) if re.match(r"^\s+\S+: ", ln) else ln for ln in kept]
    if not fits():   # the why lines (indented prose without a colon or a marker)
        kept = [ln for ln in kept if not (re.match(r"^\s{3}[^→⚑✎\[]", ln) and ": " not in ln[:40])]
    text = "\n".join(kept)
    budget = max_chars - len(tail) - 2
    if len(text) > budget:
        text = text[:budget - 1].rstrip() + "…"
    return text + "\n" + tail
