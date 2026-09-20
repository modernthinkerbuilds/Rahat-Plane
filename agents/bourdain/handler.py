"""agents.bourdain.handler — Bourdain's deterministic pipeline for the
trip slice (PRD v0.4 §16 S0+): "where do we eat", saved places first,
one live search when the seed comes up short, saves, verdicts, /list.

Rungs (cheap first, the model last; PRD §7.1):
  corrections to the last answer → save → recall / list → a location
  statement → an eat / coffee / sweets ask → help.

Everything in here works with no model. The only model call is
live.search, and only when the seed has nothing for the ask or the
owner says "something else". `now` is injected (the runner passes the
trip's local time; tests pass a fixed clock).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Callable
from urllib.parse import parse_qs, unquote_plus, urlparse

from agents.bourdain import live, resolve, state
from bridges.events.digest import md_escape, md_url

logger = logging.getLogger("bourdain")

PICKS = 3
CITY_LABEL = {"new_york": "New York", "boston": "Boston"}
_MEAL_LABEL = {"coffee": "Coffee", "dessert": "Dessert", "pastry": "Pastry",
               "bagels": "Bagels", "pizza": "Pizza", "thai": "Thai",
               "burmese": "Burmese", "late": "Late bite",
               "breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner"}
# a meal ask also accepts these seed meal keys
_MEAL_ALSO = {"breakfast": ("bagels", "pastry"), "pastry": ("dessert", "bagels"),
              "late": ("dinner",), "coffee": ("pastry",), "dessert": ("pastry",)}
_CUISINES = ("pizza", "thai", "burmese", "bagels")

HELP = (
    "I'm Bourdain — where to eat, your own places first.\n"
    "\n"
    "Just ask: \"where should we eat\", \"coffee near the hotel\", "
    "\"pizza tonight\", \"anything from my list near me\".\n"
    "Then: \"more\" · \"which one\" · \"something else\" (live search) · "
    "\"just me\" / \"we\" to change who's eating.\n"
    "Tell me where you are: \"at the hotel\", \"in Chelsea\", "
    "\"in Cambridge\".\n"
    "After a meal: \"loved it\", \"just ok\", \"don't go back\".\n"
    "\n"
    "  • `/eat [meal]` · `/coffee` · `/dessert`\n"
    "  • `/list` — your saved places for the city you're in\n"
    "  • `save <name>, <where you heard>` or paste a Maps link or a list\n"
    "  • `/who` — who's eating, which leg, where I'm ranking from\n"
    "  • `/household` · `/join <code>` · `/help`"
)

_BANNED_WORDS = ("celiac-safe", "zero risk", "cross-contamination")


# ── the trip context ──────────────────────────────────────────────────
def _ctx(cid: str, now: datetime) -> dict:
    trip = state.trip()
    leg = resolve.leg_for(trip, now)
    stays = trip.get("stays") or {}
    stay = stays.get(leg) if leg else None
    party_default = "we" if "wife" in (trip.get("party_default") or "") else "me"
    return {"trip": trip, "leg": leg, "stay": stay or {},
            "party_default": party_default}


def _where(cid: str, text: str, ctx: dict, now: datetime) -> dict | None:
    """{'label','city','area','from_stay'} or None (ask once)."""
    typed = resolve.location_from_text(text)
    if typed:
        if typed["is_stay"]:
            if not ctx["leg"]:
                return None
            loc = {"label": _stay_label(ctx["stay"]), "city": ctx["leg"],
                   "area": None, "from_stay": True}
        else:
            city = typed["city"] or ctx["leg"]
            if not city:
                return None
            loc = {"label": typed["label"], "city": city,
                   "area": typed["label"], "from_stay": False}
        state.set_location(cid, loc["label"], city=loc["city"],
                           area=loc["area"], now=now)
        return loc
    saved = state.location(cid, now)
    if saved:
        return {"label": saved["label"], "city": saved.get("city") or ctx["leg"],
                "area": saved.get("area"),
                "from_stay": saved["label"] == _stay_label(ctx["stay"])}
    if ctx["leg"]:
        return {"label": _stay_label(ctx["stay"]), "city": ctx["leg"],
                "area": None, "from_stay": True}
    return None


def _stay_label(stay: dict) -> str:
    if not stay:
        return "your stay"
    name = stay.get("name") or "your stay"
    addr = stay.get("address")
    return f"{name} ({addr})" if addr else name


def _minutes_word(stay: dict) -> str:
    return "drive" if "driv" in (stay.get("minutes_are") or "") else "walk"


# ── candidate pool ────────────────────────────────────────────────────
_GF_NO_RE = re.compile(r"\bno\s+(?:[a-z-]+\s+){0,3}(?:gf|gluten[\s-]?free)\b", re.I)


def _gf_missing(p: dict) -> str | None:
    """The owner's line says there is NO GF item here ("no GF crust",
    "espresso, but no GF pastry", "no baked GF item")."""
    him = p.get("him") or ""
    return him if _GF_NO_RE.search(him) else None


def _fits_meal(p: dict, meal: str, cuisine: str | None) -> bool:
    meals = set(p.get("meals") or [])
    if cuisine:
        return cuisine in meals
    if meal in meals:
        return True
    return any(m in meals for m in _MEAL_ALSO.get(meal, ()))


def _fits_party(p: dict, party: str) -> bool:
    tags = set(p.get("tags") or [])
    if "bar" in tags or "bar_led" in tags:
        return False
    if party == "we" and ("seafood_led" in tags or "seafood" in tags):
        return False
    if party == "we" and (p.get("her") or "—") == "—":
        return False
    return True


_GF_ASK_RE = re.compile(r"\b(gluten[\s-]?free|gf|looted free)\b", re.I)
_GF_YES_RE = re.compile(r"\b(gf|gluten[\s-]?free)\b", re.I)


def _has_gf(p: dict) -> bool:
    """A named GF item for the owner (not a 'no GF' line)."""
    him = p.get("him") or ""
    if _gf_missing(p):
        return False
    return bool(_GF_YES_RE.search(him)) or "gf_only" in set(p.get("tags") or [])


def _pool(city: str, meal: str, cuisine: str | None, party: str,
          area: str | None, exclude: set[str],
          want_gf: bool = False) -> list[dict]:
    saved = state.saved_ids()
    verdicts = state.latest_verdicts()
    rows = []
    for p in state.places(city):
        pid = p["place_id"]
        if pid in exclude or verdicts.get(pid) == "avoid":
            continue
        if not _fits_meal(p, meal, cuisine) or not _fits_party(p, party):
            continue
        is_saved = pid in saved
        if want_gf and not _has_gf(p) and not is_saved:
            continue                      # "gluten free" asked: GF only
        mins = p.get("minutes_from_stay")
        in_area = bool(area) and area.lower() in (p.get("area") or "").lower()
        below = 1 if ("below your usual bar" in (p.get("rating_text") or "")
                      or "below_bar" in set(p.get("tags") or [])) else 0
        gf_gap = 1 if _gf_missing(p) or (want_gf and not _has_gf(p)) else 0
        rows.append((0 if is_saved else 1, gf_gap, 0 if in_area else 1,
                     below, 99 if mins is None else int(mins),
                     -(p.get("rating_num") or 0), p, is_saved))
    rows.sort(key=lambda r: r[:6])
    out = []
    for _s, _g, _a, _b, _m, _r, p, is_saved in rows:
        p = dict(p, _saved=is_saved, _save=saved.get(p["place_id"], {}))
        out.append(p)
    return out


# ── rendering (PRD §8) ────────────────────────────────────────────────
def _pick_lines(i: int, p: dict, stay: dict, live_pick: bool = False) -> list[str]:
    star = "★ " if p.get("_saved") else ""
    tags = set(p.get("tags") or [])
    labels = []
    if p.get("list_label"):
        labels.append(p["list_label"])
    if live_pick or "live" in tags:
        labels.append("(live)")
    if "small_group" in tags or (p.get("locations") or 1) >= 2 and \
            (p.get("locations") or 1) < 20:
        labels.append("(small group)")
    if "below_bar" in tags or "below your usual bar" in (p.get("rating_text") or ""):
        labels.append("below your usual bar")
    mins = p.get("minutes_from_stay")
    dist = (f"~{mins} min {_minutes_word(stay)}" if mins is not None
            else "minutes n/a")
    cuisine = ", ".join(m for m in (p.get("meals") or [])
                        if m in _CUISINES or m == "coffee") or \
        (p.get("meals") or ["—"])[0]
    rating = (p.get("rating_text") or "").split("—")[0].strip()
    head = f"{i}. {star}{md_escape(p['name'])} · {md_escape(cuisine)}"
    if rating:
        head += f" · {md_escape(rating)}"
    if p.get("area"):
        head += f" · {md_escape(p['area'])}"
    head += f" · {dist}"
    if labels:
        head += " " + " ".join(labels)
    lines = [head]
    hours = p.get("hours_text") or "hours unknown"
    lines.append(f"   _{md_escape(hours)} — check before you go_")
    dish = f"   you: {md_escape(p.get('him') or '—')} · her: {md_escape(p.get('her') or '—')}"
    gf = _gf_missing(p)
    if gf:
        dish = f"   you: *{md_escape(gf)}* · her: {md_escape(p.get('her') or '—')}"
    lines.append(dish)
    why = []
    if p.get("_saved"):
        why.append(f"on your list ({p['_save'].get('source') or 'you'})")
    if p.get("why"):
        why.append(p["why"])
    elif rating:
        why.append(rating)
    wl = f"   why: {md_escape(' · '.join(why[:2]))}" if why else ""
    if p.get("map_url"):
        wl = (wl or "  ") + f" — [map]({md_url(p['map_url'])})"
    if wl:
        lines.append(wl)
    note = p.get("note") or ""
    if "closed" in note.lower() or "moved" in note.lower():
        lines.append(f"   _moved/closed: {md_escape(note)}_")
    if p.get("transit"):
        lines.append(f"   _{md_escape(p['transit'])}_")
    return lines


def _render(header: str, picks: list[dict], stay: dict, footer: list[str],
            live_pick: bool = False) -> str:
    lines = [f"*{md_escape(header)}*", ""]
    for i, p in enumerate(picks, 1):
        lines += _pick_lines(i, p, stay, live_pick)
        lines.append("")
    lines += [f"_{f}_" for f in footer if f]
    text = "\n".join(lines).rstrip()
    low = text.lower()
    for w in _BANNED_WORDS:
        assert w not in low, f"banned wording: {w}"
    return text


# ── the eat ask ───────────────────────────────────────────────────────
def handle_eat(cid: str, text: str, *, now: datetime,
               llm: Callable[[str], str] | None = None,
               force_live: bool = False, exclude: set[str] | None = None,
               party_override: str | None = None,
               meal_override: str | None = None,
               only_saved: bool = False) -> str:
    ctx = _ctx(cid, now)
    where = _where(cid, text, ctx, now)
    if where is None:
        return ("Where are you? Say \"at the hotel\", a neighborhood "
                "(\"in Chelsea\", \"in Cambridge\"), or a corner "
                "(\"23rd and 7th\") and I'll rank from there.")
    meal, said = (meal_override, True) if meal_override else \
        resolve.meal_from_text(text, now)
    cuisine = meal if meal in _CUISINES else None
    craving = None if cuisine else resolve.cuisine_from_text(text)
    if cuisine or craving:
        meal = "dinner" if now.hour >= 15 else "lunch"
    if craving and not said:
        cuisine = craving                  # the seed rarely holds it → live
    party, _ = (party_override, True) if party_override else \
        resolve.party_from_text(text, ctx["party_default"])
    city = where["city"]
    stay = (ctx["trip"].get("stays") or {}).get(city) or ctx["stay"]
    exclude = set(exclude or ())
    want_gf = bool(_GF_ASK_RE.search(text or ""))
    pool = _pool(city, meal, cuisine, party, where.get("area"), exclude,
                 want_gf=want_gf)
    if only_saved:
        pool = [p for p in pool if p.get("_saved")]
    footer: list[str] = []
    live_pick = False
    if force_live or not pool:
        found, err = live.search(city=city, city_label=CITY_LABEL.get(city, city),
                                 near=where["label"], meal=meal, party=party,
                                 cuisine=cuisine, now=now, llm=llm)
        if err:
            footer.append("Live research is down (model capped) — "
                          "answering from what I know.")
        elif found:
            saved = state.saved_ids()
            found = [dict(p, _saved=p["place_id"] in saved, _save={})
                     for p in found if p["place_id"] not in exclude]
            pool = found + [p for p in pool if p["place_id"] not in
                            {f["place_id"] for f in found}]
            live_pick = True
        elif force_live:
            footer.append("Live search found nothing that passes your "
                          "rules; here is what I know.")
    picks = pool[:PICKS]
    party_txt = "you + wife" if party == "we" else "just you"
    meal_txt = _MEAL_LABEL.get(cuisine or meal, (cuisine or meal).title())
    near_txt = ("near " + where["label"]) if where.get("from_stay") else \
        f"near {where['label']} (minutes from your stay)"
    header = " · ".join([meal_txt + ("" if said else " now"), party_txt,
                         near_txt, f"{now:%a %-I:%M %p} ET"])
    if not picks:
        body = (f"*{md_escape(header)}*\n\nNothing in your list or my notes "
                f"fits {meal_txt.lower()} here"
                + (f" for {party_txt}" if party == 'we' else '') + ".")
        if footer:
            body += "\n\n_" + footer[0] + "_"
        else:
            body += "\n\nSay \"something else\" for a live search, or `/list`."
        state.set_last_answer(cid, {"meal": meal, "cuisine": cuisine,
                                    "party": party, "city": city,
                                    "near": where["label"], "shown": [],
                                    "pool": [], "at": now.strftime("%Y-%m-%d %H:%M")})
        return body
    if not live_pick:
        footer.append("Hours were read on the seed date, not live — check "
                      "before you go. Say \"more\", \"which one\", or "
                      "\"something else\" for a live search.")
    else:
        footer.append("(live) picks were just found and saved; hours from "
                      "the web, check before you go.")
    state.set_last_answer(cid, {
        "meal": meal, "cuisine": cuisine, "party": party, "city": city,
        "near": where["label"], "shown": [p["place_id"] for p in picks],
        "pool": [p["place_id"] for p in pool],
        "at": now.strftime("%Y-%m-%d %H:%M")})
    return _render(header, picks, stay, footer, live_pick)


# ── corrections to the last answer ────────────────────────────────────
_MORE_RE = re.compile(r"^\s*(more|other options|others|next|another)\b", re.I)
_WHICH_RE = re.compile(r"^\s*(which one|which|pick one|just one|best one|"
                       r"you choose|your pick)\b\??", re.I)
_ELSE_RE = re.compile(r"\b(something else|search|surprise me|anything else|"
                      r"look online|live search)\b", re.I)
_VERDICT_WORDS = (r"loved it|loved|amazing|great|just ok|it was ok|okay|fine|"
                  r"don'?t go back|never again|didn'?t like|not good|skip it")
_VERDICT_RE = re.compile(
    r"^\s*(?:(?P<n>\d)\s*[:.\-]?\s*)?(?:(?P<name>[A-Za-z'&. ]{2,40}?)\s*[:\-]\s*)?"
    r"(?P<v>" + _VERDICT_WORDS + r")\b[!. ]*$", re.I)
_VERDICT_AFTER_RE = re.compile(
    r"^\s*(?P<v>" + _VERDICT_WORDS + r")\b[:\-, ]*"
    r"(?:(?P<n>\d)|(?P<name>[A-Za-z'&. ]{2,40}?))\s*[!.]*$", re.I)
_VERDICT_MAP = {"loved it": "loved", "loved": "loved", "amazing": "loved",
                "great": "loved", "just ok": "ok", "it was ok": "ok",
                "okay": "ok", "fine": "ok", "don't go back": "avoid",
                "dont go back": "avoid", "never again": "avoid",
                "didn't like": "avoid", "didnt like": "avoid",
                "not good": "avoid", "skip it": "avoid"}


def _handle_correction(cid: str, text: str, last: dict, now: datetime,
                       llm) -> str | None:
    if _MORE_RE.match(text):
        shown = set(last.get("shown") or [])
        by_id = {p["place_id"]: p for p in state.places(last["city"])}
        saved = state.saved_ids()
        rest = [dict(by_id[i], _saved=i in saved, _save=saved.get(i, {}))
                for i in last.get("pool") or [] if i in by_id and i not in shown]
        if not rest:
            return handle_eat(cid, "", now=now, llm=llm, force_live=True,
                              exclude=shown, party_override=last["party"],
                              meal_override=last.get("cuisine") or last["meal"])
        picks = rest[:PICKS]
        ctx = _ctx(cid, now)
        stay = (ctx["trip"].get("stays") or {}).get(last["city"]) or ctx["stay"]
        last["shown"] = list(shown | {p["place_id"] for p in picks})
        state.set_last_answer(cid, last)
        return _render(f"More {_MEAL_LABEL.get(last.get('cuisine') or last['meal'], '')} · "
                       f"{'you + wife' if last['party'] == 'we' else 'just you'} · near {last['near']}",
                       picks, stay, ["Say \"which one\" for a single pick."])
    if _WHICH_RE.match(text):
        shown = last.get("shown") or []
        if not shown:
            return None
        by_id = {p["place_id"]: p for p in state.places(last["city"])}
        p = by_id.get(shown[0])
        if not p:
            return None
        saved = state.saved_ids()
        p = dict(p, _saved=p["place_id"] in saved, _save=saved.get(p["place_id"], {}))
        ctx = _ctx(cid, now)
        stay = (ctx["trip"].get("stays") or {}).get(last["city"]) or ctx["stay"]
        return _render("My pick", [p], stay,
                       ["Nearest that fits everyone; say \"more\" if not."])
    if _ELSE_RE.search(text):
        return handle_eat(cid, text, now=now, llm=llm, force_live=True,
                          exclude=set(last.get("shown") or []),
                          party_override=last["party"],
                          meal_override=last.get("cuisine") or last["meal"])
    m = _VERDICT_RE.match(text) or _VERDICT_AFTER_RE.match(text)
    if m and m.group("v"):
        shown = last.get("shown") or []
        by_id = {p["place_id"]: p for p in state.places(last["city"])}
        target = None
        if m.group("n") and shown:
            idx = int(m.group("n")) - 1
            target = by_id.get(shown[idx]) if 0 <= idx < len(shown) else None
        elif m.group("name"):
            target = state.find_place(m.group("name"), last["city"])
        elif shown:
            target = by_id.get(shown[0])
        if not target:
            return ("Which place? Say \"loved 2\", \"loved <name>\", or "
                    "\"don't go back: <name>\".")
        verdict = _VERDICT_MAP.get(m.group("v").lower().replace("'", "'"),
                                   _VERDICT_MAP.get(m.group("v").lower(), "been"))
        state.add_verdict(target["place_id"], by=cid, verdict=verdict,
                          reason=text[:120], party=last.get("party", ""),
                          now=now)
        word = {"loved": "loved", "ok": "just ok", "avoid": "off the list for you"}[verdict]
        return f"Noted: {md_escape(target['name'])} — {word}."
    return None


# ── saves ─────────────────────────────────────────────────────────────
_SAVE_RE = re.compile(r"^\s*(?:/save\s+|save\s+|add\s+|bookmark\s+|put\s+)(?P<rest>.+)$",
                      re.I | re.S)
_URL_RE = re.compile(r"https?://\S+")


def _name_from_maps_url(url: str) -> str | None:
    try:
        q = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    for key in ("query", "q"):
        if q.get(key):
            return unquote_plus(q[key][0]).split(",")[0][:80]
    m = re.search(r"/place/([^/]+)", url)
    return unquote_plus(m.group(1)).replace("+", " ")[:80] if m else None


def handle_save(cid: str, text: str, *, now: datetime) -> str | None:
    ctx = _ctx(cid, now)
    city = ctx["leg"] or resolve.city_from_text(text)
    names: list[tuple[str, str]] = []       # (name, source)
    urls = _URL_RE.findall(text)
    for u in urls:
        n = _name_from_maps_url(u)
        if n:
            names.append((n, "maps link"))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    body = _SAVE_RE.match(text)
    if body and not urls:
        rest = body.group("rest")
        rest = re.sub(r"^(to|on)\s+my\s+\w+\s+list\s*:?\s*", "", rest, flags=re.I)
        rest = re.sub(r"\s+(to|on)\s+my\s+(\w+\s+)?list\b.*$", "", rest, flags=re.I)
        name, _, src = rest.partition(",")
        names.append((name.strip(" .:"), src.strip() or "you"))
    elif len(lines) >= 3 and not urls and not body:
        head = lines[0].lower()
        items = lines[1:] if any(w in head for w in ("places", "list", "try")) else lines
        for ln in items:
            ln = re.sub(r"^\s*(?:[-•*]|\d+[.)])\s*", "", ln)
            if ln:
                names.append((ln.split(",")[0].strip()[:80], "your list"))
    if not names:
        return None
    if not city:
        return "Which city is that for? Say \"in New York\" or \"in Boston\" with it."
    saved_names, seen = [], set()
    for name, source in names:
        key = state.norm_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        p = state.find_place(name, city)
        pid = p["place_id"] if p else state.upsert_place(
            {"name": name, "list": "yours", "city": city, "meals": [],
             "hours_text": "not checked yet", "him": "—", "her": "—",
             "map_url": "https://www.google.com/maps/search/?api=1&query="
                        + live._q(f"{name} {CITY_LABEL.get(city, city)}"),
             "note": "saved by name; not checked yet"}, now=now)
        state.save_place(pid, by=cid, source=source, city=city, now=now)
        flags = []
        if p and (p.get("rating_num") or 5) < 4.3:
            flags.append("below your usual bar")
        if p and (p.get("locations") or 1) >= 20:
            flags.append("chain")
        if not p:
            flags.append("new to me — I'll show it when it fits an ask")
        saved_names.append(name + (f" ({', '.join(flags)})" if flags else ""))
    if not saved_names:
        return None
    return ("Saved to your list: " + "; ".join(md_escape(s) for s in saved_names)
            + ". `/list` shows it.")


# ── lists ─────────────────────────────────────────────────────────────
_LIST_RE = re.compile(r"^\s*(?:/list|my list|my places|saved places|"
                      r"what'?s on my list)\b\s*(?P<city>.*)$", re.I)
_FROM_LIST_RE = re.compile(r"\b(from my list|on my list)\b", re.I)


def handle_list(cid: str, text: str, *, now: datetime) -> str:
    ctx = _ctx(cid, now)
    m = _LIST_RE.match(text)
    city = (resolve.city_from_text(m.group("city")) if m else None) or \
        resolve.city_from_text(text) or ctx["leg"]
    saved = state.saved_ids()
    rows = [p for p in state.places(city) if p["place_id"] in saved] if city \
        else [p for p in state.places() if p["place_id"] in saved]
    if not rows:
        return "Your list is empty here. `save <name>, <where you heard>` adds one."
    rows.sort(key=lambda p: (99 if p.get("minutes_from_stay") is None
                             else p["minutes_from_stay"], p["name"]))
    stay = (ctx["trip"].get("stays") or {}).get(city) or {}
    lines = [f"*Your list · {CITY_LABEL.get(city, city or 'everywhere')} · nearest first*", ""]
    for p in rows:
        mins = p.get("minutes_from_stay")
        dist = f"~{mins} min {_minutes_word(stay)}" if mins is not None else "minutes n/a"
        meals = ", ".join(p.get("meals") or []) or "—"
        line = (f"★ {md_escape(p['name'])} · {md_escape(meals)} · {dist} · "
                f"{md_escape(p.get('hours_text') or '')}")
        if p.get("map_url"):
            line += f" — [map]({md_url(p['map_url'])})"
        lines.append(line)
    return "\n".join(lines)


# ── who / where ───────────────────────────────────────────────────────
def handle_who(cid: str, *, now: datetime) -> str:
    ctx = _ctx(cid, now)
    loc = state.location(cid, now)
    lines = ["*Who, where, when*"]
    lines.append(f"Eating: {'you + wife' if ctx['party_default'] == 'we' else 'just you'} by default — say \"just me\" or \"we\" any time.")
    if ctx["leg"]:
        lines.append(f"Leg: {CITY_LABEL.get(ctx['leg'], ctx['leg'])} · stay: {md_escape(_stay_label(ctx['stay']))} · {md_escape(ctx['stay'].get('dates') or '')}")
        if ctx["stay"].get("note"):
            lines.append(f"_{md_escape(ctx['stay']['note'])}_")
    else:
        lines.append("No trip leg today — tell me where you are.")
    lines.append(f"Ranking from: {md_escape(loc['label']) if loc else 'the stay'}"
                 + (f" (until {loc['expires'][-5:]})" if loc else ""))
    lines.append(f"Clock: {now:%a %b %-d, %-I:%M %p} ET")
    return "\n".join(lines)


# ── route ─────────────────────────────────────────────────────────────
_LOC_ONLY_RE = re.compile(r"^\s*(?:i'?m|we'?re|were|now)?\s*(?:at|in|near)\s+.+$", re.I)
_CORRECTION_WINDOW_MIN = 30


def route(text: str, *, chat_id: str | int, now: datetime | None = None,
          llm: Callable[[str], str] | None = None) -> str:
    cid = str(chat_id)
    now = now or datetime.now()
    text = (text or "").strip()
    low = text.lower()
    if not text:
        return HELP
    if low.startswith("/help") or low in ("/start", "/bourdain", "hi", "hello"):
        return HELP
    if low.startswith("/who") or low.startswith("/where"):
        return handle_who(cid, now=now)
    if low.startswith("/list") or _LIST_RE.match(text):
        return handle_list(cid, text, now=now)
    if low.startswith("/eat"):
        return handle_eat(cid, text[4:].strip() or "eat", now=now, llm=llm)
    if low.startswith("/coffee"):
        return handle_eat(cid, "coffee " + text[7:], now=now, llm=llm)
    if low.startswith("/dessert"):
        return handle_eat(cid, "dessert " + text[8:], now=now, llm=llm)

    # A link or a pasted list is a save, whatever came before it.
    if _URL_RE.search(text) or len([ln for ln in text.splitlines()
                                    if ln.strip()]) >= 3:
        out = handle_save(cid, text, now=now)
        if out:
            return out

    # Corrections to the last answer (within the window).
    last = state.last_answer(cid)
    if last:
        try:
            age = (now - datetime.strptime(last["at"], "%Y-%m-%d %H:%M")).total_seconds() / 60
        except (KeyError, ValueError):
            age = 1e9
        if 0 <= age <= _CORRECTION_WINDOW_MIN:
            out = _handle_correction(cid, text, last, now, llm)
            if out:
                return out
            party, said = resolve.party_from_text(text, "")
            if said and not resolve.is_eat_ask(text):
                return handle_eat(cid, text, now=now, llm=llm,
                                  party_override=party,
                                  meal_override=last.get("cuisine") or last["meal"])

    # Save: "save X", a Maps link, a pasted list.
    out = handle_save(cid, text, now=now)
    if out:
        return out

    # "anything from my list near me" → saved places only.
    if _FROM_LIST_RE.search(text):
        return handle_eat(cid, text, now=now, llm=llm, only_saved=True)

    # A bare location statement → remember it, confirm.
    if _LOC_ONLY_RE.match(text) and not resolve.is_eat_ask(text):
        ctx = _ctx(cid, now)
        where = _where(cid, text, ctx, now)
        if where:
            return (f"Got it — ranking from {md_escape(where['label'])} for the "
                    f"next 3 hours. Ask \"where should we eat\" or \"coffee\".")

    if resolve.is_eat_ask(text) or _ELSE_RE.search(text):
        if _ELSE_RE.search(text) and not resolve.is_eat_ask(text):
            return handle_eat(cid, text, now=now, llm=llm, force_live=True)
        return handle_eat(cid, text, now=now, llm=llm)

    return ("I didn't catch that. Try \"where should we eat\", \"coffee near "
            "the hotel\", `/list`, or `/help`.")
