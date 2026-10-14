"""bridges.events.readers — the feeds Genie reads without a model (2026-10-14).

Owner, 2026-10-11: "I look at the Palo Alto, Mountain View, Berkeley,
Oakland, Redwood City, San Carlos calendars … none of the events show up."
The research (claude/genie_event_feeds_2026-10-11.md) found why: Genie's
library sources took the 25-item RSS (two days of events) through a
model, twenty of its 25 sources were grounded searches that surface a
fraction of what the calendars hold, and the Mountain View city page had
served nothing since August. Libraries, museums and arts venues publish
whole calendars as JSON, iCal or RSS; Genie read none of them.

Each reader turns one platform's response into event dicts in code: no
model call, every event in the feed, times in the household's zone, a
cancellation or a closure dropped, an audience tag on every event the
feed labels.

  kind             platform                       what Genie reads
  bibliocommons    BiblioCommons (library)        /v2/libraries/{slug}/events/search?startDate=…
  libcal           Springshare LibCal (library)   /ajax/calendar/list?c=…&page=N
  communico        Communico / libnet (library)   /eeventcaldata?event_type=0&req={…}
  tribe            WordPress The Events Calendar  /wp-json/tribe/events/v1/events?start_date=…
  localist         Localist (Stanford)            /api/2/events?start=…&venue_id=…
  livewhale        LiveWhale (UC Berkeley)        /live/json/events/…/start_date/…/
  spektrix         Spektrix ticketing             /{client}/api/v3/events
  squarespace_rss  Squarespace                    ?format=rss (the date is in the link)
  ical             any iCalendar feed             RFC 5545; UTC, TZID and all-day aware

URL tokens, filled at fetch time: {start} and {end} (ISO dates, today and
the horizon), {days} (the horizon), {page}.

Audience tags (into `categories`, where agents.genie.handler already
matches them): infant, toddler, kids, teens, adults, and "adults-only"
when a door policy is stated. A feed that labels "Babies" or "Toddlers" is the
honest source for "infant friendly events this weekend".

Hermetic: under RAHAT_TEST_MODE=1 nothing touches the wire unless an
`http` seam is passed.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Callable
from urllib.parse import quote

logger = logging.getLogger(__name__)

PACIFIC = "America/Los_Angeles"
CODE_HORIZON_DAYS = 35          # free to read, so further than the model kinds' 21
MAX_EVENTS = 400
MAX_PAGES = 8
USER_AGENT = "rahat-genie-events/1.0 (a household's calendar reader)"

KINDS = ("bibliocommons", "libcal", "communico", "tribe", "localist", "livewhale",
         "spektrix", "squarespace_rss", "ical")


# ── time ───────────────────────────────────────────────────────────────
def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — no tz database: treat as local
        return None


def to_pacific(dt: datetime) -> datetime:
    """An aware datetime in the household's zone; a naive one unchanged."""
    if dt.tzinfo is None:
        return dt
    z = _zone(PACIFIC)
    return dt.astimezone(z).replace(tzinfo=None) if z else dt.replace(tzinfo=None)


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _parse_iso(s: str | None) -> datetime | None:
    """'2026-10-17T10:30', '2026-10-17 10:30:00', '2026-10-15T00:00:00-07:00',
    '2026-10-17T07:00:00Z', '2026-10-17' → datetime (aware when zoned)."""
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d")
        return datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None


def _local(s: str | None) -> datetime | None:
    dt = _parse_iso(s)
    return to_pacific(dt) if dt else None


# ── audiences ──────────────────────────────────────────────────────────
_AUDIENCE_WORDS: tuple[tuple[str, str], ...] = (
    ("infant", r"\bbab(?:y|ies)\b|\binfants?\b|\bnewborns?\b|\blap[\s-]?sit\b|\b0\s*[-–]\s*(?:2|3|5)\b|\bunder\s+2\b"
               r"|\bbirth\b|\bearly childhood\b|\byoung children\b|\b0\s*[-–]\s*12\s*months\b|\bages?\s+0\b|\bprewalkers?\b"),
    ("toddler", r"\btoddlers?\b|\bpre-?school(?:ers?)?\b|\bpre-?k\b|\blittle ones\b|\bearly childhood\b|\byoung children\b"
                r"|\b(?:0|1|2|18\s*mos\.?|18\s*months)\s*[-–]\s*(?:3|4|5)\b|\bages?\s+(?:1|2|3)\b(?![\d+])|\b(?:2|3)\s*[-–]\s*5\b"),
    ("kids", r"\bkids?\b|\bchild(?:ren)?\b|\bfamil(?:y|ies)\b|\ball\s+ages\b|\bschool[\s-]age\b|\b6\s*[-–]\s*1[01]\b"
             r"|\belementary\b|\btweens?\b|\bpre-?teens?\b|\byouth\b|\bstory\s?time\b"),
    ("teens", r"(?<!pre-)(?<!pre)\bteens?\b|\btweens?\b|\byoung adults?\b"),
    ("adults", r"\badults?\b|\bseniors?\b|\b19\+|\b55\+|\bgrown-?ups?\b"),
)
_AUDIENCE_RES = tuple((tag, re.compile(words, re.I)) for tag, words in _AUDIENCE_WORDS)
# "Adults, ages 18+" is a library's ordinary adult audience; these words
# mean a door policy.
_ADULTS_ONLY = ("21+", "adults only", "adult only", "adults-only", "nightlife", "21 and over", "ages 21",
                "must be 21", "over 21", "21 & over", "21 and up")
_CANCELLED_RE = re.compile(r"\b(cancel+ed|cancelled|canceled|postponed)\b", re.I)
_CLOSURE_RE = re.compile(r"\b(closure|closed|closing early|modified hours)\b", re.I)
_HOURS_RE = re.compile(r"\b(museum|weekend|holiday|today'?s)?\s*(hours|schedule)\b|\bopen\s+\d{1,2}(:\d{2})?\s*(am|pm)?\s*[-–]"
                       r"|^\s*(?:holiday|weekend|museum)(?:\s+week)?\s+\d{1,2}(?::\d{2})?\s*[-–]", re.I)
_FILLER_RE = re.compile(r"^(indoor exhibits|outdoor experiences|drone plex|general admission|birthday\s*[-–]|"
                        r"members?[- ]only|member volunteer|full\s*\||back to school \w+ 20\d\d|library late opening|"
                        r"free flu vaccine|after school meals|free meals)", re.I)


def audience_tags(*texts: str) -> list[str]:
    """Tags for the audience words in `texts` (labels, categories, the
    title): infant, toddler, kids, teens, adults; "adults-only" when a
    door policy (21+, NightLife) is stated and no young audience is."""
    blob = " " + " ".join(t for t in texts if t).lower() + " "
    tags: list[str] = [tag for tag, rx in _AUDIENCE_RES if rx.search(blob)]
    young = any(t in tags for t in ("infant", "toddler", "kids", "teens"))
    if not young and any(w in blob for w in _ADULTS_ONLY):
        return ["adults", "adults-only"]
    if "adults" in tags and young:
        tags.remove("adults")            # "Adults, Kids" is a family event
    return tags


def is_noise(title: str, *labels: str) -> bool:
    """A cancellation, a closure notice, an opening-hours entry or an
    always-on filler row: not an event to plan around."""
    t = (title or "").strip()
    blob = " ".join([t, *labels])
    return bool(_CANCELLED_RE.search(t) or _CLOSURE_RE.search(blob) or _HOURS_RE.search(t)
                or _FILLER_RE.search(t) or "always on" in blob.lower())


# ── the shell every reader shares ──────────────────────────────────────
def _event(source: dict, *, title: str, start: datetime | None, end: datetime | None = None,
           venue: str = "", city: str = "", url: str = "", labels: tuple[str, ...] = (),
           extra_categories: tuple[str, ...] = ()) -> dict | None:
    title = _html.unescape(re.sub(r"\s+", " ", title or "")).strip()
    if not title or start is None:
        return None
    if is_noise(title, *labels):
        return None
    cats = list(source.get("categories") or [])
    for c in extra_categories:
        if c and c not in cats:
            cats.append(c)
    for t in audience_tags(*labels, title):
        if t not in cats:
            cats.append(t)
    return {"title": title[:160], "start_ts": _fmt(start), "end_ts": _fmt(end),
            "venue": _html.unescape(venue or source.get("venue") or "").strip()[:120],
            "city": _html.unescape(city or source.get("city") or "").strip()[:60],
            "url": (url or "")[:300], "categories": cats}


def _window(events: list[dict], today: datetime | None, horizon_days: int | None) -> list[dict]:
    """Today onward, up to the horizon; one row per run (an exhibition that
    repeats daily is listed once, on its first day in the window), and one
    row a week for a daily offering (a museum's drop-in playscape listed
    every day). A weekly storytime keeps every date."""
    lo = today.strftime("%Y-%m-%d") if today else None
    hi = (today + timedelta(days=horizon_days)).strftime("%Y-%m-%d") if today and horizon_days else None
    kept = [e for e in sorted(events, key=lambda x: x["start_ts"])
            if (not lo or e["start_ts"][:10] >= lo) and (not hi or e["start_ts"][:10] <= hi)]
    days_of: dict[tuple[str, str], set[str]] = {}
    for e in kept:
        days_of.setdefault(_key(e), set()).add(e["start_ts"][:10])
    out: list[dict] = []
    seen_runs: set[tuple[str, str]] = set()
    seen_weeks: set[tuple[str, str, str]] = set()
    for e in kept:
        day = e["start_ts"][:10]
        key = _key(e)
        next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        if e.get("end_ts") and e["end_ts"][:10] > next_day:
            if key in seen_runs:
                continue
            seen_runs.add(key)
        elif len(days_of[key]) >= 5:
            week = datetime.strptime(day, "%Y-%m-%d").strftime("%G-%V")
            if (key[0], key[1], week) in seen_weeks:
                continue
            seen_weeks.add((key[0], key[1], week))
        out.append(e)
    return out[:MAX_EVENTS]


def _key(e: dict) -> tuple[str, str]:
    return (e["title"].casefold(), (e.get("venue") or "").casefold())


def expand_url(url: str, today: datetime, horizon_days: int, page: int = 1) -> str:
    end = today + timedelta(days=horizon_days)
    return (url.replace("{start}", today.strftime("%Y-%m-%d")).replace("{end}", end.strftime("%Y-%m-%d"))
            .replace("{days}", str(horizon_days)).replace("{page}", str(page)))


def _with_page(url: str, page: int) -> str:
    if re.search(r"([?&])page=\d+", url):
        return re.sub(r"([?&])page=\d+", lambda m: f"{m.group(1)}page={page}", url)
    return url + ("&" if "?" in url else "?") + f"page={page}"


# ── BiblioCommons ──────────────────────────────────────────────────────
def parse_bibliocommons(text: str, source: dict) -> tuple[list[dict], bool]:
    """(events, has_more). The gateway's events/search response: ids in
    `events.results`, records in `entities.events`, lookups beside them."""
    d = json.loads(text)
    ents = d.get("entities") or {}
    records = ents.get("events") or {}
    audiences = {k: (v or {}).get("name", "") for k, v in (ents.get("eventAudiences") or {}).items()}
    types = {k: (v or {}).get("name", "") for k, v in (ents.get("eventTypes") or {}).items()}
    locations = ents.get("locations") or {}
    slug = ""
    m = re.search(r"/libraries/([^/]+)/", source.get("url") or "")
    if m:
        slug = m.group(1)
    order = (d.get("events") or {}).get("results") or list(records.keys())
    out: list[dict] = []
    for eid in order:
        rec = records.get(eid) or {}
        de = rec.get("definition") or {}
        if de.get("isCancelled") or rec.get("isCancelled"):
            continue
        type_names = [types.get(t, "") for t in de.get("typeIds") or []]
        aud_names = [audiences.get(a, "") for a in de.get("audienceIds") or []]
        loc = locations.get(de.get("branchLocationId") or "") or {}
        venue = loc.get("name") or ""
        city = ((loc.get("address") or {}).get("city") or "") if loc else ""
        url = f"https://{slug}.bibliocommons.com/v2/events/{eid}" if slug else ""
        ev = _event(source, title=de.get("title") or "", start=_local(de.get("start")), end=_local(de.get("end")),
                    venue=venue, city=city, url=url, labels=tuple(aud_names + type_names),
                    extra_categories=tuple(t.lower() for t in type_names if t.lower() in ("storytime", "concert", "performance")))
        if ev:
            out.append(ev)
    pg = (d.get("events") or {}).get("pagination") or {}
    has_more = bool(pg) and int(pg.get("page") or 1) < int(pg.get("pages") or 1)
    return out, has_more


# ── LibCal ─────────────────────────────────────────────────────────────
def parse_libcal(text: str, source: dict) -> tuple[list[dict], bool]:
    d = json.loads(text)
    results = d.get("results") or []
    out: list[dict] = []
    for r in results:
        aud = [a.get("name", "") for a in r.get("audiences") or [] if isinstance(a, dict)]
        cats = [c.get("name", "") for c in r.get("categories_arr") or [] if isinstance(c, dict)]
        start = _local(r.get("startdt"))
        if start and r.get("all_day"):
            start = start.replace(hour=0, minute=0, second=0)
        ev = _event(source, title=r.get("title") or "", start=start, end=_local(r.get("enddt")),
                    url=r.get("url") or "", labels=tuple(aud + cats),
                    extra_categories=tuple(c.lower() for c in cats if c.lower() in ("storytime", "concert")))
        if ev:
            out.append(ev)
    total = int(d.get("total_results") or 0)
    per = int(d.get("perpage") or len(results) or 1)
    m = re.search(r"[?&]page=(\d+)", source.get("_url", "") or "")
    page = int(m.group(1)) if m else 1
    return out, bool(results) and page * per < total


# ── Communico ──────────────────────────────────────────────────────────
def parse_communico(text: str, source: dict) -> tuple[list[dict], bool]:
    d = json.loads(text)
    rows = d if isinstance(d, list) else (d.get("events") or d.get("data") or [])
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ages = r.get("agesArray") or [a.strip() for a in str(r.get("ages") or "").split(",") if a.strip()]
        tags = r.get("tagsArray") or []
        ev = _event(source, title=r.get("title") or "", start=_local(r.get("event_start") or r.get("raw_start_time")),
                    end=_local(r.get("event_end") or r.get("raw_end_time")),
                    venue=(r.get("library") or r.get("location") or "").strip(), url=r.get("url") or "",
                    labels=tuple(list(ages) + list(tags) + [str(r.get("changed") or "")]))
        if ev:
            out.append(ev)
    return out, False


# ── The Events Calendar (WordPress) ────────────────────────────────────
def _json_body(text: str) -> dict:
    """The JSON object in a response, even when a WordPress plugin has
    printed a <script> block in front of it (cdm.org does)."""
    try:
        return json.loads(text)
    except ValueError:
        tail = text[text.rfind("</script>") + 9:] if "</script>" in text else text
        i = tail.find("{")
        if i < 0:
            raise
        return json.loads(tail[i:])


def parse_tribe(text: str, source: dict) -> tuple[list[dict], bool]:
    d = _json_body(text)
    out: list[dict] = []
    for r in d.get("events") or []:
        cats = [c.get("name", "") for c in r.get("categories") or [] if isinstance(c, dict)]
        tags = [t.get("name", "") for t in r.get("tags") or [] if isinstance(t, dict)]
        v = r.get("venue") if isinstance(r.get("venue"), dict) else {}
        start = _parse_iso(r.get("start_date"))
        end = _parse_iso(r.get("end_date"))
        ev = _event(source, title=r.get("title") or "", start=start, end=end,
                    venue=v.get("venue") or "", city=v.get("city") or "", url=r.get("url") or "",
                    labels=tuple(cats + tags),
                    extra_categories=tuple(c.lower() for c in cats if c.lower() in ("family", "storytime", "concert", "film", "theatre", "theater")))
        if ev:
            out.append(ev)
    nxt = d.get("next_rest_url")
    source["_next_url"] = nxt or ""
    return out, bool(nxt)


# ── Localist (Stanford) ────────────────────────────────────────────────
def parse_localist(text: str, source: dict) -> tuple[list[dict], bool]:
    d = json.loads(text)
    out: list[dict] = []
    for wrap in d.get("events") or []:
        e = wrap.get("event") if isinstance(wrap, dict) else None
        if not isinstance(e, dict) or e.get("status") not in (None, "live"):
            continue
        filters = e.get("filters") or {}
        names = [x.get("name", "") for k in ("event_audience", "event_types", "event_subject")
                 for x in (filters.get(k) or []) if isinstance(x, dict)]
        is_exhibition = any(n.lower() == "exhibition" for n in names)
        for inst in e.get("event_instances") or []:
            i = inst.get("event_instance") if isinstance(inst, dict) else None
            if not isinstance(i, dict):
                continue
            start = _local(i.get("start"))
            end = _local(i.get("end"))
            if start and i.get("all_day"):
                start = start.replace(hour=0, minute=0, second=0)
            if is_exhibition and end is None and e.get("last_date"):
                end = _parse_iso(e.get("last_date"))
            ev = _event(source, title=e.get("title") or "", start=start, end=end,
                        venue=e.get("location_name") or "", url=e.get("localist_url") or e.get("url") or "",
                        labels=tuple(names), extra_categories=("exhibition",) if is_exhibition else ())
            if ev:
                out.append(ev)
    pg = d.get("page") or {}
    source["_next_page"] = pg.get("next_page")
    return out, bool(pg.get("next_page"))


# ── LiveWhale (UC Berkeley) ────────────────────────────────────────────
def parse_livewhale(text: str, source: dict) -> tuple[list[dict], bool]:
    d = json.loads(text)
    rows = d.get("data") if isinstance(d, dict) else d
    out: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("is_canceled"):
            continue
        start = _local(r.get("date_iso"))
        if start and r.get("is_all_day"):
            start = start.replace(hour=0, minute=0, second=0)
        labels = tuple(str(t.get("title", t) if isinstance(t, dict) else t) for t in (r.get("tags") or []))
        ev = _event(source, title=r.get("title") or "", start=start, end=_local(r.get("date2_iso")),
                    venue=str((r.get("location") or {}).get("title", "") if isinstance(r.get("location"), dict) else r.get("location") or ""),
                    url=r.get("url") or "", labels=labels)
        if ev:
            out.append(ev)
    meta = (d.get("meta") or {}) if isinstance(d, dict) else {}
    return out, int(meta.get("page") or 1) < int(meta.get("total_pages") or 1)


# ── Spektrix ───────────────────────────────────────────────────────────
def parse_spektrix(text: str, source: dict, today: datetime | None = None) -> tuple[list[dict], bool]:
    rows = json.loads(text)
    out: list[dict] = []
    floor = (today or datetime.now()).strftime("%Y-%m-%d")
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        first, last = _parse_iso(r.get("firstInstanceDateTime")), _parse_iso(r.get("lastInstanceDateTime"))
        start = first if first and _fmt(first)[:10] >= floor else (last if last and _fmt(last)[:10] >= floor else None)
        genre = str(r.get("attribute_Genre") or "")
        ev = _event(source, title=r.get("name") or "", start=start, url=r.get("webUrl") or "",
                    labels=(genre, str(r.get("attribute_SeasonType") or "")),
                    extra_categories=(genre.lower().split("/")[0].strip(),) if genre else ())
        if ev:
            if first and last and _fmt(first)[:10] != _fmt(last)[:10] and start == last:
                ev["title"] = f"{ev['title']} (last date)"
            out.append(ev)
    return out, False


# ── Squarespace RSS ────────────────────────────────────────────────────
_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
_LINK_DATE_RE = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/")
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b", re.I)


def _tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
    if not m:
        return ""
    s = re.sub(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", m.group(1), flags=re.S)
    return _html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def parse_squarespace_rss(text: str, source: dict) -> tuple[list[dict], bool]:
    """Items whose link carries the event date (/calendar/2026/11/8/slug).
    The item's pubDate is the publish date, so it is ignored."""
    out: list[dict] = []
    for block in _ITEM_RE.findall(text or ""):
        link = _tag(block, "link")
        m = _LINK_DATE_RE.search(link)
        if not m:
            continue
        y, mo, da = (int(x) for x in m.groups())
        desc = _tag(block, "description")
        tm = _TIME_RE.search(desc)
        hour, minute = 0, 0
        if tm:
            hour = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "p" else 0)
            minute = int(tm.group(2) or 0)
        try:
            start = datetime(y, mo, da, hour, minute)
        except ValueError:
            continue
        ev = _event(source, title=_tag(block, "title"), start=start, url=link,
                    labels=(_tag(block, "category"), desc[:300]))
        if ev:
            out.append(ev)
    return out, False


# ── iCalendar ──────────────────────────────────────────────────────────
_ICS_PROP_RE = re.compile(r"^([A-Z-]+)((?:;[^:]+)?):(.*)$")


def _unescape_ics(s: str) -> str:
    return (s.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";")
            .replace("\\\\", "\\").strip())


def _ics_datetime(params: str, value: str, default_zone: str | None) -> datetime | None:
    value = value.strip()
    if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", value):
        try:
            return datetime.strptime(value[:8], "%Y%m%d")
        except ValueError:
            return None
    m = re.match(r"(\d{8}T\d{6})(Z?)", value)
    if not m:
        return None
    try:
        naive = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if m.group(2) == "Z":
        z = _zone("UTC")
        return to_pacific(naive.replace(tzinfo=z)) if z else naive
    tz = re.search(r"TZID=([^;:]+)", params)
    name = tz.group(1).strip('"') if tz else default_zone
    if name and name != PACIFIC:
        z = _zone(name)
        if z:
            return to_pacific(naive.replace(tzinfo=z))
    return naive


def parse_ical(text: str, source: dict, *, today: datetime | None = None,
               horizon_days: int | None = None) -> list[dict]:
    """VEVENT blocks → event dicts. UTC ("Z") and TZID times become the
    household's local time; all-day DATE values keep 00:00; a folded line
    is unfolded; "\\," is a comma. Past events are dropped before any cap
    (the old reader kept the feed's first 40 rows, which on a LibCal feed
    were a month of past storytimes). RRULE masters are listed once, at
    their DTSTART."""
    text = re.sub(r"\r?\n[ \t]", "", text or "")
    default_zone = None
    m = re.search(r"^X-WR-TIMEZONE:(.+)$", text, re.M)
    if m:
        default_zone = m.group(1).strip()
    events: list[dict] = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        props: dict[str, tuple[str, str]] = {}
        for line in block.splitlines():
            pm = _ICS_PROP_RE.match(line.strip())
            if pm:
                props.setdefault(pm.group(1), (pm.group(2), pm.group(3)))
        if "DTSTART" not in props:
            continue
        start = _ics_datetime(*props["DTSTART"], default_zone)
        end = _ics_datetime(*props["DTEND"], default_zone) if "DTEND" in props else None
        if "RRULE" in props:
            logger.debug("RRULE listed once at DTSTART: %s", props["RRULE"][1][:80])
        desc = _unescape_ics(props.get("DESCRIPTION", ("", ""))[1])[:400]
        cats = _unescape_ics(props.get("CATEGORIES", ("", ""))[1])
        ev = _event(source, title=_unescape_ics(props.get("SUMMARY", ("", ""))[1]), start=start, end=end,
                    venue=_unescape_ics(props.get("LOCATION", ("", ""))[1]),
                    url=props.get("URL", ("", ""))[1].strip(), labels=(cats, desc))
        if ev:
            events.append(ev)
    return _window(events, today, horizon_days) if today else events[:MAX_EVENTS]


# ── fetch + dispatch ───────────────────────────────────────────────────
_FAMILY_AUDIENCE_WORDS = ("babies", "baby", "infant", "toddler", "preschool", "pre-school", "kids", "children",
                          "child", "family", "families", "all ages", "youth")


def bibliocommons_family_ids(text: str) -> str:
    """The library's own audience ids for babies through families, from
    the `eventAudiences` lookup any search response carries. Each
    library numbers its audiences differently, so the {family} token on
    a BiblioCommons URL is filled from the feed itself."""
    try:
        auds = (json.loads(text).get("entities") or {}).get("eventAudiences") or {}
    except (ValueError, AttributeError):
        return ""
    ids = [k for k, v in auds.items()
           if any(w in str((v or {}).get("name") or "").lower() for w in _FAMILY_AUDIENCE_WORDS)]
    return ",".join(sorted(ids))


_PARSERS: dict[str, Callable] = {
    "bibliocommons": parse_bibliocommons, "libcal": parse_libcal, "communico": parse_communico,
    "tribe": parse_tribe, "localist": parse_localist, "livewhale": parse_livewhale,
    "spektrix": parse_spektrix, "squarespace_rss": parse_squarespace_rss,
}


def _get(url: str, http: Callable[[str], str] | None) -> str | None:
    if http is not None:
        return http(url)
    if os.getenv("RAHAT_TEST_MODE") == "1":
        return None                     # hermetic: no wire without a seam
    import requests
    resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/calendar, application/rss+xml, text/xml, */*;q=0.5"})
    resp.raise_for_status()
    return resp.text


def fetch(source: dict, *, today: datetime | None = None, http: Callable[[str], str] | None = None,
          horizon_days: int = CODE_HORIZON_DAYS) -> list[dict]:
    """Every event of one source, today through the horizon, paging as the
    platform does. Raises on a transport failure (the caller logs it and
    counts the refresh as unproductive, like every other kind)."""
    today = today or datetime.now()
    kind = source.get("kind") or ""
    first = expand_url(source["url"], today, horizon_days)
    if "{family}" in first:
        probe = _get(re.sub(r"limit=\d+", "limit=20", first.replace("{family}", "")), http)
        first = first.replace("{family}", bibliocommons_family_ids(probe) if probe else "")
    if kind == "ical":
        text = _get(first, http)
        return parse_ical(text, source, today=today, horizon_days=horizon_days) if text else []
    parser = _PARSERS.get(kind)
    if parser is None:
        raise ValueError(f"no reader for kind {kind!r}")
    events: list[dict] = []
    url, page = first, 1
    src = dict(source)
    while url and page <= MAX_PAGES:
        src["_url"] = url
        text = _get(url, http)
        if not text:
            break
        got, more = parser(text, src, today) if kind == "spektrix" else parser(text, src)
        events += got
        if not more:
            break
        page += 1
        if kind == "tribe":
            url = src.get("_next_url") or ""
        elif kind == "localist":
            url = _with_page(first, int(src.get("_next_page") or page))
        else:
            url = _with_page(first, page)
    return _window(events, today, horizon_days)


__all__ = ["KINDS", "CODE_HORIZON_DAYS", "PACIFIC", "audience_tags", "is_noise", "expand_url", "fetch",
           "bibliocommons_family_ids",
           "parse_bibliocommons", "parse_libcal", "parse_communico", "parse_tribe", "parse_localist",
           "parse_livewhale", "parse_spektrix", "parse_squarespace_rss", "parse_ical", "to_pacific"]
