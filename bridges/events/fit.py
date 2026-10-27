"""bridges.events.fit — which listings are worth this household's
weekend (owner, 2026-10-27).

THE COMPLAINT. "I never get any exciting events. All I get is the de
Young, the Botanical Garden, a beach chili cook-off ... I need some
really good events": the babywearing dance class, the bookstore's
storytimes, "specialties". They were in the inventory all along (the
Oct 14 outings list came from these feeds); the digest showed the first
six listings of a day by time, about six of 170, and all-day items (5K
runs, free-admission days) sort first.

THE RULE (code, no model). Each listing gets a family-fit score and a
short reason, from its title, venue, categories and city:
  + made for the household's little ones (a baby time, a lapsit, a
    babywearing dance; a toddler or preschool storytime), for families;
  + a special format (an author or bookstore storytime, a concert for
    little ones, a sensory time, a farm or animal day, a puppet show);
  + an occasion (a festival, a holiday, a cultural celebration) and the
    household's own interests (a profile list);
  + close to home; − far away (another county, out of the area);
  − school-age or teen only, adult classes, meetings and volunteer
    days, races.
A day shows its best listings that clear FLOOR, in time order, each
with its reason. The household's specifics (home, who is in it, its
interests) come from the caller's FitProfile; this module knows only
generic rules and a public table of city points.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

FLOOR = 20                     # a listing below this is not a pick
NEAR_KM, MID_KM, FAR_KM = 25.0, 45.0, 75.0

# Rough city centres (public geography, ~1 km), for "how far is it".
CITY_POINTS: dict[str, tuple[float, float]] = {
    # Santa Clara County
    "san jose": (37.34, -121.89), "santa clara": (37.35, -121.96), "sunnyvale": (37.37, -122.04),
    "mountain view": (37.39, -122.08), "palo alto": (37.44, -122.14), "stanford": (37.43, -122.17),
    "los altos": (37.38, -122.11), "los altos hills": (37.38, -122.14), "cupertino": (37.32, -122.03),
    "campbell": (37.29, -121.95), "los gatos": (37.23, -121.96), "saratoga": (37.26, -122.02),
    "monte sereno": (37.24, -121.99), "milpitas": (37.43, -121.90), "morgan hill": (37.13, -121.65),
    "gilroy": (37.01, -121.57),
    # San Mateo County
    "menlo park": (37.45, -122.18), "atherton": (37.46, -122.20), "east palo alto": (37.47, -122.14),
    "redwood city": (37.49, -122.24), "san carlos": (37.51, -122.26), "belmont": (37.52, -122.28),
    "san mateo": (37.56, -122.32), "foster city": (37.56, -122.27), "burlingame": (37.58, -122.37),
    "millbrae": (37.60, -122.39), "san bruno": (37.63, -122.41), "south san francisco": (37.65, -122.41),
    "daly city": (37.69, -122.47), "pacifica": (37.61, -122.49), "half moon bay": (37.46, -122.43),
    "woodside": (37.43, -122.25), "portola valley": (37.38, -122.24), "brisbane": (37.68, -122.40),
    # San Francisco
    "san francisco": (37.77, -122.42), "sf": (37.77, -122.42),
    # Alameda County
    "fremont": (37.55, -121.99), "newark": (37.53, -122.04), "union city": (37.60, -122.02),
    "hayward": (37.67, -122.08), "san leandro": (37.72, -122.16), "castro valley": (37.69, -122.09),
    "san lorenzo": (37.68, -122.12), "pleasanton": (37.66, -121.87), "livermore": (37.68, -121.77),
    "dublin": (37.70, -121.94), "oakland": (37.80, -122.27), "berkeley": (37.87, -122.27),
    "alameda": (37.77, -122.24), "albany": (37.89, -122.30), "emeryville": (37.83, -122.29),
    "piedmont": (37.82, -122.23),
    # Contra Costa County
    "richmond": (37.94, -122.35), "el cerrito": (37.92, -122.31), "walnut creek": (37.91, -122.07),
    "lafayette": (37.89, -122.12), "orinda": (37.88, -122.18), "moraga": (37.83, -122.13),
    "danville": (37.82, -122.00), "san ramon": (37.78, -121.98), "concord": (37.98, -122.03),
    "pleasant hill": (37.95, -122.06), "martinez": (38.02, -122.13), "antioch": (38.00, -121.81),
    "pittsburg": (38.03, -121.88), "clayton": (37.94, -121.94), "hercules": (38.02, -122.29),
    "pinole": (38.00, -122.30), "oakley": (38.00, -121.71), "brentwood": (37.93, -121.70),
    "el sobrante": (37.98, -122.29), "san pablo": (37.96, -122.35), "kensington": (37.91, -122.28),
    # Marin County
    "sausalito": (37.86, -122.49), "mill valley": (37.91, -122.55), "san rafael": (37.97, -122.53),
    "novato": (38.11, -122.57), "corte madera": (37.93, -122.53), "larkspur": (37.93, -122.54),
    "fairfax": (37.99, -122.59), "san anselmo": (37.97, -122.56), "tiburon": (37.87, -122.46),
    "point reyes station": (38.07, -122.81),
    # Santa Cruz and Monterey counties
    "santa cruz": (36.97, -122.03), "scotts valley": (37.05, -122.01), "capitola": (36.98, -121.95),
    "aptos": (36.98, -121.90), "watsonville": (36.91, -121.76), "felton": (37.05, -122.07),
    "boulder creek": (37.13, -122.12), "ben lomond": (37.09, -122.09), "la selva beach": (36.93, -121.86),
    "monterey": (36.60, -121.89), "carmel": (36.56, -121.92), "carmel-by-the-sea": (36.56, -121.92),
    "pacific grove": (36.62, -121.92), "salinas": (36.68, -121.66), "seaside": (36.61, -121.85),
    "marina": (36.68, -121.80), "hollister": (36.85, -121.40), "san juan bautista": (36.85, -121.54),
    # North Bay
    "napa": (38.30, -122.29), "sonoma": (38.29, -122.46), "santa rosa": (38.44, -122.71),
    "petaluma": (38.23, -122.64), "vallejo": (38.10, -122.26), "benicia": (38.05, -122.16),
    # region labels some feeds use instead of a city
    "east bay": (37.80, -122.27), "peninsula": (37.53, -122.30), "south bay": (37.34, -121.89),
}
_REGION_ONLY = {"", "bay area", "ca", "california", "online", "virtual"}

BABY_RE = re.compile(r"\b(?:bab(?:y|ies)|infants?|newborns?|lap[\s-]*sit|prewalkers?|bouncers?|bounce|"
                     r"baby[\s-]*wearing|babywearing|0\s*(?:-|–|to)\s*(?:12|18|24)\s*(?:mo|months?)|"
                     r"under\s*(?:12|18)\s*months?)\b", re.I)
TODDLER_RE = re.compile(r"\b(?:toddlers?|preschool(?:ers)?|little ones|tots?|wee ones|"
                        r"ages?\s*[0-3]\s*(?:-|–|to)\s*[2-6]|story\s*time|storytimes?|stories|sing[\s-]*along|"
                        r"rhymes?|music (?:and|&) movement|puppets?|marionettes?)\b", re.I)
FAMILY_RE = re.compile(r"\b(?:family|families|all ages|for kids|kids'?|children'?s|caregivers?)\b", re.I)
SPECIAL_FORMAT_RE = re.compile(
    r"\b(?:author|illustrator|book launch|book signing|museum minis|very first concerts?|family concerts?|"
    r"kids'? concerts?|children'?s concerts?|concerts? for (?:babies|little|kids|families)|story time band|"
    r"sensory|naturalist|farm tour|animal day|baby sign|sign (?:and|&) sing|dance (?:class|party)|babywearing|"
    r"baby wearing|music class|ballet|symphony|orchestra|puppet show|marionettes?|bilingual)\b", re.I)
BOOKSTORE_RE = re.compile(r"\b(?:linden tree|kepler'?s|books inc|hicklebee'?s|mrs\.? dalloway'?s|book passage|"
                          r"bookshop|bookstore)\b", re.I)
OCCASION_RE = re.compile(
    r"\b(?:festival|fest|celebrat\w*|halloween|trick[\s-]*or[\s-]*treat|trunk[\s-]*or[\s-]*treat|"
    r"d[ií]a de (?:los )?muertos|diwali|lunar new year|holiday|christmas|hanukkah|kwanzaa|thanksgiving|"
    r"new year'?s|parade|fair|carnival|grand opening|lantern|light show|fireworks|night market)\b", re.I)
SCHOOL_AGE_RE = re.compile(r"\b(?:ages?\s*(?:[6-9]|1[0-8])\s*(?:\+|and up|(?:-|–|to)\s*\d+)?|grades?\s*\d|"
                           r"k\s*(?:-|–)\s*[2-8]|kindergarten|tweens?|teens?|teenagers?|middle school|high school|"
                           r"youth council|advisory council)\b", re.I)
ADULT_RE = re.compile(r"\b(?:adults?|grown[\s-]*ups? only|21\s*\+|18\s*\+|book club|esl|english conversation|"
                      r"tax|computer|tech help|resume|jobs?|career|citizenship|medicare|genealogy|knitting|crochet|"
                      r"retirement|investing|figure drawing|life drawing|wine|beer|happy hour|trivia|comedy|"
                      r"nightlife|dungeons)\b", re.I)
ADMIN_RE = re.compile(r"\b(?:meeting|council|commission|board of|hearing|volunteers?|volunteer day|intake|"
                      r"application|deadline|closed|closure|cancell?ed|conference|webinar|virtual|online|zoom|"
                      r"livestream|office hours|clean[\s-]*up)\b", re.I)
RACE_RE = re.compile(r"\b(?:5\s*k|10\s*k|half[\s-]*marathon|marathon|fun run|race|trail run|triathlon|"
                     r"run|run[\s-]*walk|trot)\b", re.I)      # "Halloween Run", a turkey trot; not "(runs to ...)"


@dataclass
class FitProfile:
    """The household, as the scorer needs it (built by the caller from its private profile)."""
    home: tuple[float, float] | None = None          # the home's city point
    little: frozenset[str] = frozenset()             # {"baby", "toddler"}: who is small now
    interests: tuple[str, ...] = ()                  # words of the household's own interests


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 -]+", " ", s.lower())).strip()


def city_point(city: str) -> tuple[float, float] | None:
    """A city's point, from the city field (or a city named inside it)."""
    c = _norm(city)
    if c in CITY_POINTS:
        return CITY_POINTS[c]
    for name in sorted(CITY_POINTS, key=len, reverse=True):
        if len(name) > 3 and re.search(rf"\b{re.escape(name)}\b", c):
            return CITY_POINTS[name]
    return None


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def score(row: dict, profile: FitProfile) -> tuple[int, list[str]]:
    """(score, reasons) for one listing."""
    title = str(row.get("title") or "")
    venue = str(row.get("venue") or "")
    cats = row.get("categories") or ""
    cats = " ".join(cats) if isinstance(cats, (list, tuple)) else str(cats)
    s, why = 0, []
    # From the title only: the feeds' audience categories tag whole family programs "infant" (a LEGO club,
    # a book sale), so they can't tell a baby time from an all-ages afternoon.
    baby = bool(BABY_RE.search(title))
    tod = bool(TODDLER_RE.search(title))
    little = (baby and "baby" in profile.little) or (tod and ("toddler" in profile.little or "baby" in profile.little))
    if baby and "baby" in profile.little:
        s += 45
        why.append("for babies")
    elif tod and ("toddler" in profile.little or "baby" in profile.little):
        s += 30
        why.append("for little ones")
    family = bool(FAMILY_RE.search(title)) or "family" in {c.strip().lower() for c in cats.split(",")}
    if family and not little:
        s += 12
        why.append("for families")
    if BOOKSTORE_RE.search(f"{title} {venue}"):
        s += 20
        why.append("bookstore")
    elif SPECIAL_FORMAT_RE.search(title):
        s += 20
        m = SPECIAL_FORMAT_RE.search(title).group(0).lower()
        why.append("author event" if m in ("author", "illustrator", "book launch", "book signing")
                   else "concert for little ones" if "concert" in m or m == "story time band" else m)
    occ = OCCASION_RE.search(title)
    if occ:
        s += 25
        word = occ.group(0)
        why.append("a celebration" if word.lower().startswith("celebrat") else word if len(word) <= 20 else "an occasion")
    for w in profile.interests:
        if w and re.search(rf"\b{re.escape(w.lower())}", title.lower()):
            s += 20
            why = [x for x in why if x != "a celebration"]
            if not any(w.lower() in x.lower() for x in why):
                why.insert(1 if why and why[0].startswith("for ") else 0, w[:1].upper() + w[1:])
            break
    if row.get("_score") is not None:
        s += int((float(row["_score"]) - 50) / 4)          # the recurring series' online reputation
    # how far
    if profile.home is not None:
        city = str(row.get("city") or "")
        pt = city_point(city) or city_point(venue)
        if pt is None:
            s += -5 if _norm(city) in _REGION_ONLY else -25
        else:
            d = km(profile.home, pt)
            if d <= NEAR_KM:
                s += 15
                why.append("close to home")
            elif d > FAR_KM:
                s -= 60
            elif d > MID_KM:
                s -= 20
    # not for this household
    if SCHOOL_AGE_RE.search(title) and not little:
        s -= 30
    if ADULT_RE.search(title) and not (little or family):
        s -= 35
    if ADMIN_RE.search(title):
        s -= 60
    if RACE_RE.search(title):
        s -= 25
    if str(row.get("start_ts") or "")[11:16] in ("", "00:00") and not occ:
        s -= 5
    seen, out = set(), []
    for w in why:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return s, out[:3]


def pick(rows: list[dict], profile: FitProfile, *, limit: int = 6, floor: int = FLOOR) -> tuple[list[dict], int]:
    """(the best rows that clear the floor, at most `limit`, in time order, each with `_fit_score` and
    `_why`; how many cleared the floor in all)."""
    scored = []
    for r in rows:
        sc, why = score(r, profile)
        if sc >= floor:
            scored.append(dict(r, _fit_score=sc, _why=why))
    scored.sort(key=lambda r: -r["_fit_score"])
    best = scored[:limit]
    best.sort(key=lambda r: str(r.get("start_ts") or ""))
    return best, len(scored)
