"""agents.bourdain.tools — the tools the executor calls (V2 §8; ADR-018
§2.2 step 3). Every fact a tool reports is an evidence record in the
knowledge base; the model picks places and never supplies a fact.

Order of truth for a lookup: the knowledge base first (facts inside
their freshness window are reused), then the web through the source
registry (grounded search for candidates and lists, robots-compliant
page reads for hours, status, ratings, menus, rooms), and every live
result is written back. State tools write through scoped state and the
knowledge base; profile edits are not a tool (they take a confirm card).

`build_registry(ctx)` returns a ToolRegistry limited to the manifest's
allow-list; `ctx` carries now, the leg, the chat, the member ids, the
trace id, the db path and the cassette dir.

Replay: the network tools are not cassetted at the tool level. Their
model calls (`core.llm_chat`) and HTTP reads (`core.httpcache`) are,
so on replay the tool code runs again and writes the same evidence
records the live run wrote — the knowledge base ends up identical.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from core import fetch as cfetch
from core import geocode as cgeo
from core import hours as chours
from core import llm_chat
from core import state_scopes as ss
from core import weather as cweather
from core.kb import geo, query, schema as kb
from core.tools import ToolRegistry, ToolResult
from agents.bourdain import profile as cprofile

AGENT = "bourdain"
LOCATION_OVERRIDE_HOURS = 3.0


# ── context helpers ─────────────────────────────────────────────────────
def _now(ctx: dict) -> datetime:
    return ctx.get("now") or datetime.now()


def _db(ctx: dict):
    return ctx.get("db_path")


def _con(ctx: dict):
    return kb.connect(_db(ctx))


def _mode(ctx: dict) -> str:
    leg = ctx.get("leg") or {}
    return "trip" if leg.get("kind") == "trip" else "home"


def _chat(ctx: dict) -> str:
    return str(ctx.get("chat_id") or "household")


def _fact_view(f: dict | None) -> dict | None:
    """One evidence record as the executor sees it: the plain value with
    its provenance beside it. A seed fact stored as {value, source,
    checked} is flattened so the model never has to unwrap it."""
    if not f:
        return None
    value, source_name, observed = f.get("value"), f.get("source_name"), f.get("observed_at")
    if isinstance(value, dict) and "value" in value:
        source_name = source_name or value.get("source")
        observed = observed or value.get("checked")
        value = value["value"]
    return {"fact_id": f["fact_id"], "claim": f["claim_type"], "value": value,
            "source_url": f.get("source_url"), "source_name": source_name,
            "observed_at": observed, "fresh": None}


def _place_view(con, p: dict, ctx: dict) -> dict:
    now = _now(ctx)
    mode = _mode(ctx)
    out = {"place_id": p["place_id"], "name": p["name"], "city": p.get("city"),
           "neighborhood": p.get("neighborhood"), "address": p.get("address"),
           "cuisine": p.get("cuisine"), "family": p.get("cuisine_family"), "price_level": p.get("price_level"),
           "status": p.get("status"), "location_count": p.get("location_count"), "founded": p.get("founded"),
           "format_tags": p.get("format_tags"), "room_tags": p.get("room_tags"), "source_list": p.get("source_list"),
           "lat": p.get("lat"), "lng": p.get("lng"), "facts": {}}
    for claim in ("hours", "status", "price", "menu", "dish_note:primary", "dish_note:spouse", "why",
                  "acclaim", "map_url", "yelp_url", "phone", "reservations", "payment", "transit",
                  "minutes_from_stay", "note", "seating", "format", "location_count"):
        f = kb.current_fact(con, "place", p["place_id"], claim)
        if f:
            v = _fact_view(f)
            v["fresh"] = kb.is_fresh(f, now, mode)
            out["facts"][claim] = v
    r = query.rating(con, p["place_id"])
    if r:
        out["rating"] = r
    origin = ctx.get("origin_latlng")
    if origin and p.get("lat") is not None:
        out["about_minutes"] = geo.about_minutes(origin[0], origin[1], p["lat"], p["lng"],
                                                 mode=(ctx.get("leg") or {}).get("minutes_mode", "drive"))
    return out


# ── argument models ─────────────────────────────────────────────────────
class NoArgs(BaseModel):
    pass


class MembersArgs(BaseModel):
    members: list[str] = Field(default_factory=list, description="member ids at the table; empty = everyone")


class NameArgs(BaseModel):
    text: str = Field(description="a place or landmark name as heard, dictation and typos included")
    city: str | None = None


class SearchPlacesArgs(BaseModel):
    family: str | None = Field(default=None, description="cuisine family, e.g. thai, korean, italian")
    city: str | None = None
    within_minutes: int | None = Field(default=None, description="travel budget from the origin")
    mode: str | None = Field(default=None, description="walk | drive | transit")
    saved_only: bool = False
    include_excluded: bool = False
    limit: int = 12


class FactsArgs(BaseModel):
    place_id: str


class WebSearchArgs(BaseModel):
    query: str = Field(description="what to find: cuisine, area, format, e.g. 'family-run Burmese restaurant near Allston Boston'")
    city: str = Field(description="the city or area the results must be in")
    want: int = Field(default=5, description="how many candidates")


class PageFactsArgs(BaseModel):
    url: str
    place_id: str | None = Field(default=None, description="the place these facts belong to, when known")
    claims: list[str] = Field(default_factory=lambda: ["hours", "status", "rating", "price", "menu", "room", "phone", "location_count"],
                              description="which claims to read from the page")


class AcclaimArgs(BaseModel):
    place_name: str
    city: str


class WeatherArgs(BaseModel):
    lat: float
    lng: float


class RouteArgs(BaseModel):
    origin: str = Field(description="a place, landmark, 'stay', 'home', or 'lat,lng'")
    destination: str
    mode: str = "drive"


class GeocodeArgs(BaseModel):
    query: str = Field(description="an address or 'name, city'")
    place_id: str | None = None


class SaveArgs(BaseModel):
    place_id: str
    member: str = "primary"
    heard_from: str | None = None
    occasion: str | None = None
    for_date: str | None = None


class VerdictArgs(BaseModel):
    kind: str = Field(description="chain | reported_score | price | room | delight | style | loved | chosen | rejected | closed | boring | wrong_claim")
    place_id: str | None = None
    class_target: str | None = Field(default=None, description="a class instead of a place, e.g. '$$$ wine bars'")
    member: str = "primary"
    value: str | None = None
    text: str | None = None


class OverrideArgs(BaseModel):
    members: list[str]
    rule: str = Field(description="e.g. 'vegetarian', 'no egg', 'no chicken', 'chicken ok'")
    scope: str = Field(default="day", description="day | session | trip")
    withdraw: bool = False


class PlanEditArgs(BaseModel):
    stops: list[str]
    note: str | None = None


class LocationArgs(BaseModel):
    label: str
    lat: float | None = None
    lng: float | None = None
    city: str | None = None


class YelpArgs(BaseModel):
    name: str
    city: str


# ── extraction schemas (what the model must return) ────────────────────
class Candidate(BaseModel):
    name: str
    address: str | None = None
    city: str | None = None
    neighborhood: str | None = None
    cuisine: str | None = None
    rating_source: str | None = None
    rating_value: float | None = None
    rating_count: int | None = None
    hours_text: str | None = None
    status: str | None = Field(default=None, description="open | closed | moved | coming_soon | seasonal_closed | unknown")
    price_level: str | None = None
    phone: str | None = None
    url: str | None = Field(default=None, description="the page the facts came from")
    is_bar_led: bool | None = None
    seafood_led: bool | None = None
    location_count: int | None = None
    notes: str | None = None


class CandidateList(BaseModel):
    candidates: list[Candidate]


class PageFacts(BaseModel):
    hours_text: str | None = None
    status: str | None = None
    rating_source: str | None = None
    rating_value: float | None = None
    rating_count: int | None = None
    price_level: str | None = None
    menu_items: list[str] = Field(default_factory=list, description="'dish — $price — note' strings seen on the page")
    room_notes: str | None = Field(default=None, description="bar, loud, packed, stroller room, highchairs, seating")
    phone: str | None = None
    location_count: int | None = None
    acclaim: list[str] = Field(default_factory=list, description="named lists or writers that praise it, with dates")
    confidence_note: str | None = None


# ── the registry ────────────────────────────────────────────────────────
def _deny_blocked_hosts(tool, args: dict, ctx: dict):
    """Pre-tool gate (PR-H5): a blocked or link-only host never reaches a fetch."""
    url = args.get("url")
    if isinstance(url, str) and url:
        from core import sources as _sources
        pol = _sources.explicit_policy(url)
        if pol and pol["policy"] in ("blocked", "link_only"):
            return f"blocked host ({pol['policy']}): {pol['why']}"
    return None


def clean_text(v):
    """Instruction-shaped text is struck wherever it sits: a whole line
    inside a block, or a short field on its own. Applied on the way into
    the knowledge base and on the way out to the model."""
    if isinstance(v, str):
        if "\n" in v:
            return cfetch.sanitize(v)[0]
        return "" if cfetch._INSTRUCTION_RE.search(v) else v
    if isinstance(v, dict):
        return {k: clean_text(x) for k, x in v.items()}
    if isinstance(v, list):
        return [clean_text(x) for x in v]
    return v


def _sanitize_values(tool, args: dict, res: ToolResult, ctx: dict) -> ToolResult:
    """Post-tool gate (PR-D6/PR-V3): text that came from the web is data,
    whichever tool carried it (the knowledge base holds web text too)."""
    if not res.ok:
        return res
    res.value = clean_text(res.value)
    return res


def build_registry(ctx: dict, *, allow: list[str] | None = None) -> ToolRegistry:
    reg = ToolRegistry(allow=allow)
    reg.pre_hooks.append(_deny_blocked_hosts)
    reg.post_hooks.append(_sanitize_values)
    prof = ctx.setdefault("profile", cprofile.load())

    # ---- profile, rules, overrides, leg, last answer (reads) ----
    @reg.tool("get_profile", "The canonical household profile, whole: every member's diet, loves and room rules.", args=MembersArgs)
    def get_profile(a: MembersArgs, c):
        members = a.members or cprofile.member_ids(prof)
        return {"members": {m: prof["members"].get(m, {"role": "guest"}) for m in members},
                "household": prof.get("household", {}), "source": prof.get("_source")}

    @reg.tool("get_household_rules", "Rules learned from the household's verdicts, with their provenance.", args=NoArgs)
    def get_household_rules(a: NoArgs, c):
        con = _con(c)
        try:
            verdicts = con.execute("SELECT kind, class_target, text, at FROM kb_verdict WHERE place_id IS NULL "
                                   "OR kind IN ('chain','price','room','style') ORDER BY at DESC LIMIT 30").fetchall()
        finally:
            con.close()
        return {"rules": prof.get("household", {}).get("rules", []),
                "verdict_rules": [{"kind": v[0], "target": v[1], "text": v[2], "at": v[3]} for v in verdicts]}

    @reg.tool("get_overrides", "Overrides and exclusions in force right now (day, session, trip scopes).", args=NoArgs)
    def get_overrides(a: NoArgs, c):
        now = _now(c)
        chat = _chat(c)
        return {"overrides": [r["value"] | {"scope": r["scope"], "until": r["expires_at"]}
                              for r in ss.get_all(AGENT, chat, "override", now=now, db_path=_db(c))],
                "exclusions": [r["value"] | {"scope": r["scope"], "until": r["expires_at"]}
                               for r in ss.get_all(AGENT, chat, "exclusion", now=now, db_path=_db(c))]}

    @reg.tool("get_leg_context", "The active leg: trip or home, the stay, the clock, the day's plan, the weather on file.", args=NoArgs)
    def get_leg_context(a: NoArgs, c):
        leg = c.get("leg") or {}
        now = _now(c)
        plan = ss.get(AGENT, _chat(c), "plan", now=now, db_path=_db(c))
        return {"leg": leg, "now": now.strftime("%Y-%m-%d %H:%M"), "weekday": now.strftime("%A"),
                "plan_today": plan, "origin": c.get("origin_label"), "mode": _mode(c)}

    @reg.tool("get_last_answer", "The previous answer in this chat: its frame, picks and pool, for follow-ups.", args=NoArgs)
    def get_last_answer(a: NoArgs, c):
        last = ss.get(AGENT, _chat(c), "last_answer", now=_now(c), db_path=_db(c)) or {}
        return {k: v for k, v in last.items() if k != "trace_id"}      # run ids are not the model's business (and break replay)

    # ---- resolution and the knowledge base ----
    @reg.tool("resolve_name", "Match a dictated, partial or misspelled name against the last answer, saves, the knowledge base, landmarks and aliases.", args=NameArgs)
    def resolve_name(a: NameArgs, c):
        con = _con(c)
        try:
            cands = []
            last = ss.get(AGENT, _chat(c), "last_answer", now=_now(c), db_path=_db(c)) or {}
            norm = kb.norm_name(a.text)
            for p in last.get("picks", []):
                n = kb.norm_name(p.get("name", ""))
                if norm and (norm in n or n in norm or _initials_match(norm, n)):
                    cands.append({"kind": "place", "place_id": p.get("place_id"), "name": p.get("name"),
                                  "why": "in the last answer", "confidence": 0.9})
            for p in query.find_place(con, a.text, city=a.city)[:5]:
                cands.append({"kind": "place", "place_id": p["place_id"], "name": p["name"], "city": p.get("city"),
                              "why": "knowledge base", "confidence": 0.8 if kb.norm_name(p["name"]) == norm else 0.6})
            lm = query.landmark(con, a.text)
            if lm:
                cands.append({"kind": "landmark", **lm, "why": "landmark", "confidence": 0.85})
            amb = con.execute("SELECT readings, rule FROM kb_alias WHERE kind='ambiguous' AND alias_norm = ?",
                              (norm,)).fetchone()
            if amb:
                cands.append({"kind": "ambiguous", "readings": json.loads(amb[0] or "[]"), "rule": amb[1],
                              "confidence": 0.5})
            return {"candidates": cands[:8], "resolved": cands[0] if len(cands) == 1 or (cands and cands[0]["confidence"] >= 0.85) else None}
        finally:
            con.close()

    @reg.tool("kb_search_places", "Places from the knowledge base: by cuisine family, within a travel budget from the origin, saves first; excluded places left out unless asked.", args=SearchPlacesArgs)
    def kb_search_places(a: SearchPlacesArgs, c):
        con = _con(c)
        try:
            now = _now(c)
            city = a.city or (c.get("leg") or {}).get("city")
            rows = query.places(con, city=city)
            if a.family:
                fam = a.family.lower()
                rows = [p for p in rows if fam in ((p.get("cuisine_family") or "") + " " + (p.get("cuisine") or "")).lower()]
            saved = query.saved_ids(con)
            if a.saved_only:
                rows = [p for p in rows if p["place_id"] in saved]
            excluded = {} if a.include_excluded else query.excluded_ids(con, now=now)
            origin = c.get("origin_latlng")
            out = []
            for p in rows:
                if p["place_id"] in excluded:
                    continue
                v = _place_view(con, p, c)
                v["saved"] = p["place_id"] in saved
                if a.within_minutes and origin and v.get("about_minutes") is not None and v["about_minutes"] > a.within_minutes:
                    continue
                out.append(v)
            out.sort(key=lambda v: (not v["saved"], v.get("about_minutes") if v.get("about_minutes") is not None else 999,
                                    -(v.get("rating") or {}).get("value", 0) if v.get("rating") else 0))
            left_out = {pid: why for pid, why in excluded.items()}
            return {"places": out[:a.limit], "count_before_limit": len(out), "excluded": len(left_out),
                    "kb_hits": len(out)}
        finally:
            con.close()

    @reg.tool("kb_facts", "Every current fact the knowledge base holds about one place, with sources and freshness.", args=FactsArgs)
    def kb_facts(a: FactsArgs, c):
        con = _con(c)
        try:
            p = query.place(con, a.place_id)
            if not p:
                return ToolResult(name="kb_facts", ok=False, error=f"no place {a.place_id}")
            v = _place_view(con, p, c)
            v["menu_items"] = [dict(zip(("name", "price", "diet_tags", "gf", "note"), r)) for r in con.execute(
                "SELECT name, price_text, diet_tags, gf, note FROM kb_menu_item WHERE place_id = ? LIMIT 40", (a.place_id,))]
            v["acclaim"] = [dict(zip(("writer", "rank", "date", "url", "quote"), r)) for r in con.execute(
                "SELECT writer, rank, date, url, quote FROM kb_acclaim WHERE place_id = ? LIMIT 10", (a.place_id,))]
            v["verdicts"] = [dict(zip(("kind", "text", "at"), r)) for r in con.execute(
                "SELECT kind, text, at FROM kb_verdict WHERE place_id = ? ORDER BY at DESC LIMIT 10", (a.place_id,))]
            return v
        finally:
            con.close()

    # ---- the web, through the source registry ----
    @reg.tool("web_search", "Grounded web search for candidate places in an area. Returns candidates with the page each fact came from; writes them to the knowledge base as live places with citations as evidence.",
              args=WebSearchArgs, kind="network", cacheable=False, returns_evidence=True, search_queries_hint=1)
    def web_search(a: WebSearchArgs, c):
        prompt = (f"Find up to {a.want} restaurants or cafés matching: {a.query}. They must be in or very near {a.city}. "
                  "For each, report only what a web page states: the full address, the cuisine, one rating with its source "
                  "and review count (never invent a number), the hours text as written, whether it is open/closed/moved, the "
                  "price level, the phone, whether the room is bar-led, whether seafood leads the menu, how many locations "
                  "the brand has, and the URL of the page the facts came from. Leave a field null when no page states it.")
        schema_hint = json.dumps({"candidates": [{k: ("number" if k in ("rating_value",) else "integer" if k in ("rating_count", "location_count")
                                                       else "boolean" if k in ("is_bar_led", "seafood_led") else "string or null")
                                                   for k in Candidate.model_fields}]})
        # Grounded search and a response schema can't be combined on every
        # Gemini tier, so the JSON shape is asked for in the prompt and
        # parsed leniently (the same contract v0.x's live search used).
        res = llm_chat.chat(AGENT, "bourdain.web_search", system="You are a careful researcher. Facts only from pages you read; null when unknown. Answer with one JSON object and nothing else.",
                            messages=[llm_chat.user_text(prompt + "\nAnswer as JSON exactly in this shape: " + schema_hint)],
                            search=True, thinking_budget=0, step="execute", mode=_mode(c), trace_id=c.get("trace_id"),
                            db_path=_db(c), cassette_dir=c.get("cassette_dir"))
        if not res.ok:
            return ToolResult(name="web_search", ok=False, error=res.error, cost_usd=res.cost_usd,
                              search_queries=res.search_queries)
        data = _json_object(res.text)
        try:
            parsed = CandidateList.model_validate(data).candidates
        except Exception as e:  # noqa: BLE001
            return ToolResult(name="web_search", ok=False, error=f"unusable search result: {str(e)[:160]}",
                              cost_usd=res.cost_usd, search_queries=res.search_queries)
        cite_urls = [ci.url for ci in res.citations if ci.url]
        out, ev_ids = [], []
        con = _con(c)
        try:
            for cand in parsed:
                written = _write_candidate(con, cand, cite_urls, c)
                ev_ids += written["fact_ids"]
                out.append(written["view"])
            con.commit()
        finally:
            con.close()
        return ToolResult(name="web_search", ok=True, value={"candidates": out, "citations": cite_urls[:10],
                                                             "queries_run": res.search_queries},
                          evidence_ids=ev_ids, cost_usd=res.cost_usd, search_queries=res.search_queries)

    @reg.tool("read_page_facts", "Read one page (own site, ordering page or aggregator) under the source rules and record the facts it states: hours, status, rating with count, price, menu items, room, phone, location count, acclaim.",
              args=PageFactsArgs, kind="network", cacheable=False, returns_evidence=True)
    def read_page_facts(a: PageFactsArgs, c):
        page = cfetch.fetch_page(a.url, now=_now(c))
        if not page.ok:
            return ToolResult(name="read_page_facts", ok=False, error=page.error or "page unreadable")
        res = llm_chat.structured(AGENT, "bourdain.read_page", system="Extract only what the page states. Null when absent. Never infer a rating or hours.",
                                  prompt=f"Page: {page.final_url}\nTitle: {page.title}\nRead at: {page.fetched_at}\n"
                                         f"Claims wanted: {', '.join(a.claims)}\n\n{page.text[:14000]}",
                                  schema=PageFacts, thinking_budget=0, step="execute", mode=_mode(c),
                                  trace_id=c.get("trace_id"), db_path=_db(c), cassette_dir=c.get("cassette_dir"))
        if not res.ok or res.parsed is None:
            return ToolResult(name="read_page_facts", ok=False, error=res.error or "extraction failed",
                              cost_usd=res.cost_usd)
        facts = PageFacts.model_validate(res.parsed)
        ev_ids: list[int] = []
        view: dict[str, Any] = {"url": page.final_url, "read_at": page.fetched_at, "trust_tier": page.trust_tier}
        if a.place_id:
            con = _con(c)
            try:
                rank = "own_site" if page.trust_tier in ("", "own_site") and "tripadvisor" not in page.final_url else (page.trust_tier or "aggregator")
                ev_ids = _write_page_facts(con, a.place_id, facts, page, rank)
                con.commit()
            finally:
                con.close()
        view["facts"] = facts.model_dump(exclude_none=True)
        view["fact_ids"] = ev_ids
        return ToolResult(name="read_page_facts", ok=True, value=view, evidence_ids=ev_ids, cost_usd=res.cost_usd)

    @reg.tool("acclaim", "Named praise for a place: food writers, ranked lists, dated. Grounded search; sources recorded.",
              args=AcclaimArgs, kind="network", cacheable=False, returns_evidence=True, search_queries_hint=1)
    def acclaim(a: AcclaimArgs, c):
        res = llm_chat.chat(AGENT, "bourdain.acclaim", system="Report only named, dated praise you can cite.",
                            messages=[llm_chat.user_text(
                                f"Which food writers, critics' lists or local guides praise {a.place_name} in {a.city}? "
                                "Give the publication, the writer if named, the date, the URL and one quoted phrase each. "
                                "Return JSON: {\"items\": [{\"publication\", \"writer\", \"date\", \"url\", \"quote\"}]}")],
                            search=True, thinking_budget=0, step="execute", mode=_mode(c),
                            trace_id=c.get("trace_id"), db_path=_db(c), cassette_dir=c.get("cassette_dir"))
        if not res.ok:
            return ToolResult(name="acclaim", ok=False, error=res.error, cost_usd=res.cost_usd, search_queries=res.search_queries)
        items = _json_items(res.text)
        urls = {ci.url for ci in res.citations if ci.url}
        kept = [it for it in items if it.get("url") and (it["url"] in urls or not urls)]
        return ToolResult(name="acclaim", ok=True, value={"items": kept[:6], "citations": sorted(urls)[:6]},
                          cost_usd=res.cost_usd, search_queries=res.search_queries)

    @reg.tool("weather", "The official forecast for a point with its issuance time and any active hazards.",
              args=WeatherArgs, kind="network", cacheable=False, returns_evidence=True)
    def weather(a: WeatherArgs, c):
        w = cweather.forecast(a.lat, a.lng, now=_now(c))
        if not w:
            return ToolResult(name="weather", ok=False, error="forecast unavailable")
        con = _con(c)
        try:
            fid = kb.add_fact(con, subject_type="weather", subject_id=f"{a.lat:.3f},{a.lng:.3f}", claim_type="forecast",
                              value=w, source_url=w["source_url"], source_name=w["source_name"], source_rank="own_site",
                              method="api", observed_at=w["observed_at"], trace_id=c.get("trace_id"), now=_now(c))
            con.commit()
        finally:
            con.close()
        return ToolResult(name="weather", ok=True, value=w, evidence_ids=[fid])

    @reg.tool("geocode", "Coordinates for an address or 'name, city' (OpenStreetMap, attribution kept). Updates the place's location when a place_id is given.",
              args=GeocodeArgs, kind="network", cacheable=False, returns_evidence=True)
    def geocode(a: GeocodeArgs, c):
        g = cgeo.geocode(a.query, now=_now(c))
        if not g:
            return ToolResult(name="geocode", ok=False, error="no match")
        ev = []
        if a.place_id:
            con = _con(c)
            try:
                fid = kb.add_fact(con, subject_type="place", subject_id=a.place_id, claim_type="geo", value=g,
                                  source_url=g["source_url"], source_name=g["source_name"], source_rank="own_site",
                                  method="api", observed_at=g["observed_at"], trace_id=c.get("trace_id"), now=_now(c))
                con.execute("UPDATE kb_location SET lat = ?, lng = ?, geocode_fact_id = ? WHERE location_id = "
                            "(SELECT location_id FROM kb_place WHERE place_id = ?)", (g["lat"], g["lng"], fid, a.place_id))
                con.commit()
                ev = [fid]
            finally:
                con.close()
        return ToolResult(name="geocode", ok=True, value=g, evidence_ids=ev)

    @reg.tool("route_time", "Estimated minutes between two places from stored coordinates, labeled 'about'. No routes API.", args=RouteArgs)
    def route_time(a: RouteArgs, c):
        con = _con(c)
        try:
            o = _point(con, a.origin, c)
            d = _point(con, a.destination, c)
        finally:
            con.close()
        if not o or not d:
            return ToolResult(name="route_time", ok=False, error=f"no coordinates for {'origin' if not o else 'destination'}")
        return {"about_minutes": geo.about_minutes(o[0], o[1], d[0], d[1], mode=a.mode), "mode": a.mode,
                "label": "about", "km": round(geo.haversine_km(o[0], o[1], d[0], d[1]), 2)}

    # ---- state writes ----
    @reg.tool("save_place", "Save a place for the household with where it was heard and an occasion.", args=SaveArgs, kind="write")
    def save_place(a: SaveArgs, c):
        con = _con(c)
        try:
            con.execute("INSERT INTO kb_save (place_id, member_id, saved_at, heard_from, occasion, for_date, trace_id) "
                        "VALUES (?,?,?,?,?,?,?)", (a.place_id, a.member, _now(c).strftime("%Y-%m-%d %H:%M:%S"),
                                                   a.heard_from, a.occasion, a.for_date, c.get("trace_id")))
            con.commit()
        finally:
            con.close()
        c.setdefault("state_writes", []).append({"kind": "save", "place_id": a.place_id, "for_date": a.for_date})
        return {"saved": a.place_id}

    @reg.tool("record_verdict", "Store a household verdict on a place or a class of places (chain, reported score, price, room, boring, loved, chosen...).", args=VerdictArgs, kind="write")
    def record_verdict(a: VerdictArgs, c):
        con = _con(c)
        try:
            con.execute("INSERT INTO kb_verdict (place_id, class_target, member_id, kind, value_json, text, at, scope, trace_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (a.place_id, a.class_target, a.member, a.kind, json.dumps({"value": a.value}) if a.value else None,
                         a.text, _now(c).strftime("%Y-%m-%d %H:%M:%S"), "standing", c.get("trace_id")))
            if a.kind in ("chosen",) and a.place_id:
                con.execute("INSERT INTO kb_visit (place_id, at, members, source, trace_id) VALUES (?,?,?,?,?)",
                            (a.place_id, _now(c).strftime("%Y-%m-%d %H:%M:%S"), json.dumps(c.get("diners") or []),
                             "chosen", c.get("trace_id")))
            con.commit()
        finally:
            con.close()
        c.setdefault("state_writes", []).append({"kind": "verdict", "verdict": a.kind, "place_id": a.place_id,
                                                 "class_target": a.class_target})
        return {"recorded": a.kind}

    @reg.tool("set_override", "A diet override for members with a scope (day by default) or its withdrawal ('egg is back').", args=OverrideArgs, kind="write")
    def set_override(a: OverrideArgs, c):
        now = _now(c)
        chat = _chat(c)
        if a.withdraw:
            n = 0
            for r in ss.get_all(AGENT, chat, "override", now=now, db_path=_db(c)):
                if set(r["value"].get("who", [])) & set(a.members) and r["value"].get("rule") == a.rule:
                    ss.withdraw(r["row_id"], now=now, db_path=_db(c)); n += 1
            c.setdefault("state_writes", []).append({"kind": "override_withdrawn", "rule": a.rule, "who": a.members})
            return {"withdrawn": n}
        rid = ss.put(AGENT, chat, "override", {"who": a.members, "rule": a.rule}, scope=a.scope, now=now,
                     source_trace=c.get("trace_id"), db_path=_db(c))
        c.setdefault("state_writes", []).append({"kind": "override", "rule": a.rule, "who": a.members, "scope": a.scope})
        return {"override_id": rid, "until": ss.expiry_for(a.scope, now)}

    @reg.tool("edit_plan", "Rewrite the rest of today's plan: the stops in order and a note; overrides stay in force.", args=PlanEditArgs, kind="write")
    def edit_plan(a: PlanEditArgs, c):
        rid = ss.put(AGENT, _chat(c), "plan", {"stops": a.stops, "note": a.note}, scope="day", now=_now(c),
                     replace=True, source_trace=c.get("trace_id"), db_path=_db(c))
        c.setdefault("state_writes", []).append({"kind": "plan_edit", "stops": a.stops})
        return {"plan_id": rid, "stops": a.stops}

    @reg.tool("set_location", "Where the household is right now (a label, optional coordinates); lasts three hours.", args=LocationArgs, kind="write")
    def set_location(a: LocationArgs, c):
        now = _now(c)
        latlng = (a.lat, a.lng) if a.lat is not None and a.lng is not None else None
        if latlng is None:
            con = _con(c)
            try:
                latlng = _point(con, a.label, c)
            finally:
                con.close()
        rid = ss.put(AGENT, _chat(c), "location", {"label": a.label, "lat": latlng[0] if latlng else None,
                                                   "lng": latlng[1] if latlng else None, "city": a.city},
                     scope="session", now=now, replace=True, expires_in_hours=LOCATION_OVERRIDE_HOURS,
                     source_trace=c.get("trace_id"), db_path=_db(c))
        if latlng:
            c["origin_latlng"] = latlng
        c["origin_label"] = a.label
        c.setdefault("state_writes", []).append({"kind": "location", "label": a.label})
        return {"location_id": rid, "label": a.label, "resolved": bool(latlng)}

    @reg.tool("yelp_link", "A search link on the household's trusted review site (never read, always linked).", args=YelpArgs)
    def yelp_link(a: YelpArgs, c):
        return {"url": "https://www.yelp.com/search?" + urllib.parse.urlencode({"find_desc": a.name, "find_loc": a.city})}

    @reg.tool("clock", "The leg's local time now.", args=NoArgs)
    def clock(a: NoArgs, c):
        now = _now(c)
        return {"now": now.strftime("%Y-%m-%d %H:%M"), "weekday": now.strftime("%A"),
                "tz": (c.get("leg") or {}).get("tz")}

    return reg


# ── helpers ─────────────────────────────────────────────────────────────
def _initials_match(heard: str, name: str) -> bool:
    """'dan b' ~ 'danbi'; 'veggie delight' ~ 'veggie galaxy' (first word)."""
    h = heard.replace(" ", "")
    hw, nw = heard.split(), name.split()
    if not h or not nw:
        return False
    return name.replace(" ", "").startswith(h) or (bool(hw) and hw[0] == nw[0] and len(hw[0]) >= 4)


def _json_object(text: str) -> dict:
    """The first JSON object in a model's text, or {}."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _json_items(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return [i for i in (items or []) if isinstance(i, dict)]


def _point(con, label: str, ctx: dict) -> tuple[float, float] | None:
    lab = (label or "").strip().lower()
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", lab)
    if m:
        return float(m.group(1)), float(m.group(2))
    if lab in ("stay", "the stay", "hotel", "the hotel", "home", "the house", "origin", "here"):
        o = ctx.get("origin_latlng")
        if o:
            return o
        leg = ctx.get("leg") or {}
        if leg.get("stay_lat") is not None:
            return (leg["stay_lat"], leg["stay_lng"])
        return None
    lm = query.landmark(con, label)
    if lm and lm.get("lat") is not None:
        return (lm["lat"], lm["lng"])
    for p in query.find_place(con, label):
        if p.get("lat") is not None:
            return (p["lat"], p["lng"])
    return None


def _write_candidate(con, cand: Candidate, cite_urls: list[str], ctx: dict) -> dict:
    cand = Candidate.model_validate(clean_text(cand.model_dump()))      # web text is data, on the way in
    now = ctx.get("now") or datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    city = (cand.city or (ctx.get("leg") or {}).get("city") or "").lower().replace(" ", "_")
    src = cand.url or (cite_urls[0] if cite_urls else None)
    rank = "search_citation" if src else "unverified"
    from core.kb.schema import location_id_for, place_id_for, norm_name
    loc_id = location_id_for(city, cand.address, None, None, name_hint=cand.name)
    con.execute("INSERT OR IGNORE INTO kb_location (location_id, address, neighborhood, city, created_at) VALUES (?,?,?,?,?)",
                (loc_id, cand.address, cand.neighborhood, city, now_s))
    pid = place_id_for(cand.name, city, address=cand.address)
    fam = kb.CUISINE_FAMILIES.get((cand.cuisine or "").lower())
    con.execute(
        "INSERT INTO kb_place (place_id, name, name_norm, location_id, cuisine, cuisine_family, price_level, status, "
        "location_count, format_tags, room_tags, source_list, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(place_id) DO UPDATE SET cuisine=COALESCE(kb_place.cuisine, excluded.cuisine), "
        "cuisine_family=COALESCE(kb_place.cuisine_family, excluded.cuisine_family), "
        "price_level=COALESCE(kb_place.price_level, excluded.price_level), updated_at=excluded.updated_at",
        (pid, cand.name, norm_name(cand.name), loc_id, (cand.cuisine or "").lower() or None, fam, cand.price_level,
         cand.status if cand.status in ("open", "closed", "moved", "coming_soon", "seasonal_closed") else "open",
         cand.location_count, "[]", "[]", "live", now_s, now_s))
    fact_ids: list[int] = []

    def f(claim, value, **kw):
        fid = kb.add_fact(con, subject_type="place", subject_id=pid, claim_type=claim, value=value, source_url=src,
                          source_name=cand.rating_source if claim == "rating" else ("search" if src else "model"),
                          source_rank=rank, method="search_citation",
                          observed_at=now_s, trace_id=ctx.get("trace_id"), now=now, **kw)
        fact_ids.append(fid)
        return fid

    if cand.rating_value is not None and cand.rating_source:
        fid = f("rating", {"value": cand.rating_value, "source": cand.rating_source, "count": cand.rating_count})
        if cand.rating_count:
            con.execute("INSERT OR REPLACE INTO kb_rating (fact_id, place_id, source, value, count, observed_at) VALUES (?,?,?,?,?,?)",
                        (fid, pid, cand.rating_source, cand.rating_value, cand.rating_count, now_s))
    if cand.hours_text:
        f("hours", {"value": cand.hours_text})
    if cand.status:
        fid = f("status", cand.status)
        con.execute("UPDATE kb_place SET status_fact_id = ? WHERE place_id = ?", (fid, pid))
    if cand.price_level:
        f("price", {"value": cand.price_level})
    if cand.phone:
        f("phone", {"value": cand.phone})
    if cand.is_bar_led is not None:
        f("room", {"bar_led": cand.is_bar_led, "seafood_led": cand.seafood_led})
    if cand.location_count is not None:
        f("location_count", cand.location_count)
    if cand.notes:
        f("note", {"value": cand.notes})
    view = cand.model_dump(exclude_none=True)
    view.update({"place_id": pid, "source_url": src, "evidence_rank": rank, "fact_ids": fact_ids})
    return {"view": view, "fact_ids": fact_ids}


def _write_page_facts(con, place_id: str, facts: "PageFacts", page: cfetch.Page, rank: str) -> list[int]:
    facts = PageFacts.model_validate(clean_text(facts.model_dump()))     # web text is data, on the way in
    ids: list[int] = []
    now = datetime.now()

    def f(claim, value):
        fid = kb.add_fact(con, subject_type="place", subject_id=place_id, claim_type=claim, value=value,
                          source_url=page.final_url or page.url, source_name=page.title or page.final_url,
                          source_rank=rank, method="fetch", observed_at=page.fetched_at, now=now)
        ids.append(fid)
        return fid

    if facts.hours_text:
        fid = f("hours", {"value": facts.hours_text})
        parsed = chours.parse(facts.hours_text)
        for iv in parsed.intervals:
            con.execute("INSERT OR IGNORE INTO kb_hours (fact_id, place_id, weekday, open_min, close_min, service, note) "
                        "VALUES (?,?,?,?,?,?,?)", (fid, place_id, iv.weekday, iv.open_min, iv.close_min, iv.service, iv.text))
    if facts.status:
        fid = f("status", facts.status)
        if facts.status in ("open", "closed", "moved", "coming_soon", "seasonal_closed"):
            con.execute("UPDATE kb_place SET status = ?, status_fact_id = ? WHERE place_id = ?", (facts.status, fid, place_id))
    if facts.rating_value is not None and facts.rating_source:
        fid = f("rating", {"value": facts.rating_value, "source": facts.rating_source, "count": facts.rating_count})
        if facts.rating_count:
            con.execute("INSERT OR REPLACE INTO kb_rating (fact_id, place_id, source, value, count, observed_at) VALUES (?,?,?,?,?,?)",
                        (fid, place_id, facts.rating_source, facts.rating_value, facts.rating_count, page.fetched_at))
    if facts.price_level:
        f("price", {"value": facts.price_level})
        con.execute("UPDATE kb_place SET price_level = COALESCE(price_level, ?) WHERE place_id = ?", (facts.price_level, place_id))
    if facts.menu_items:
        fid = f("menu", {"value": facts.menu_items})
        for item in facts.menu_items[:40]:
            name, _, rest = item.partition(" — ")
            con.execute("INSERT INTO kb_menu_item (place_id, fact_id, name, price_text, note) VALUES (?,?,?,?,?)",
                        (place_id, fid, name.strip() or item[:80], rest.split(" — ")[0] if rest else None, rest))
    if facts.room_notes:
        f("room", {"value": facts.room_notes})
    if facts.phone:
        f("phone", {"value": facts.phone})
    if facts.location_count is not None:
        f("location_count", facts.location_count)
        con.execute("UPDATE kb_place SET location_count = ? WHERE place_id = ?", (facts.location_count, place_id))
    for a in facts.acclaim[:6]:
        f("acclaim", {"value": a})
    return ids
