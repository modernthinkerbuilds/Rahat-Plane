"""agents.bourdain.harness — Bourdain as tenant 1 of the harness
(ADR-018 §2.4): the manifest, the prompts, the outcome library, the
checklist, the tools, the profile and how the session is read and
written. The eight steps themselves live in `core/harness/run.py`.

What this module decides for Bourdain:
  * the session: the active leg from the knowledge base, the origin
    (a location override for three hours, else the stay, else home),
    who is speaking (the chat's household role), the diners by default,
    the overrides and exclusions in force, today's plan, the last answer;
  * the Understand prompt (the frame schema plus the session), the
    moment and the rules text the executor sees;
  * the verify context, the render settings and the floor.

Members are roles, never names; the profile is data in the vault.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core import judge as cjudge
from core import manifest as cmanifest
from core import state_scopes as ss
from core import verify as cverify
from core.harness import control as ccontrol
from core.harness import render as hrender
from core.harness.answer import Answer
from core.harness.contract import Contract, load_library
from core.harness.evidence import Evidence
from core.harness.frame import Frame, ReferencePlace, frame_json_schema
from core.harness.run import RunResult
from core.kb import query, schema as kb
from core.tools import ToolRegistry
from agents.bourdain import profile as cprofile
from agents.bourdain import tools as btools

AGENT = "bourdain"
ROLE_TO_MEMBER = {"primary": "primary", "spouse": "spouse", "group": "primary"}


class BourdainTenant:
    name = AGENT

    def __init__(self, *, manifest: cmanifest.Manifest | None = None, profile: dict | None = None):
        self.manifest = manifest or cmanifest.load(AGENT)
        self.library = load_library(self.manifest.resolve(self.manifest.outcome_library) or
                                    Path(__file__).with_name("outcomes.json"))
        sp = self.manifest.resolve(self.manifest.system_prompt)
        self.system_prompt = sp.read_text(encoding="utf-8") if sp and sp.exists() else ""
        self.banned_phrases = list(self.manifest.banned_phrases)
        cl = self.manifest.resolve(self.manifest.judge_checklist)
        self.checklist = cjudge.load_checklist(AGENT, cl) if cl and cl.exists() else None
        self.judge_config = self.manifest.judge
        self.budgets = self.manifest.budgets
        self.latency = self.manifest.latency
        self._profile = profile

    # ── session ───────────────────────────────────────────────────────────
    def profile(self) -> dict:
        return self._profile if self._profile is not None else cprofile.load()

    def speaker_for(self, chat_id: str) -> str:
        """Which member is asking. The chat configured as BOURDAIN_PRIMARY_CHAT
        is the profile's primary, and the household's other adult chat is the
        spouse. The household registry (shared with Genie) records the order
        the chats joined in — the first adult in is "primary" — not who the
        profile's members are: LIVE 2026-10-04 it named the owner "spouse", so
        every "I" was read as the spouse, with the spouse's rules and words."""
        cid = str(chat_id)
        primary = (os.getenv("BOURDAIN_PRIMARY_CHAT") or "").strip()
        if primary and cid == primary:
            return "primary"
        try:
            from agents.bourdain import state as old_state
            role = old_state.household_role_for(cid)
        except Exception:  # noqa: BLE001 - the household registry is optional here
            role = None
        if primary and role in ("primary", "spouse"):
            return "spouse"                    # the household's other adult
        return ROLE_TO_MEMBER.get(role or "", "primary")

    def build_ctx(self, chat_id: str, now: datetime, text: str, *, db_path: str | None, cassette_dir: str | None,
                  trace_id: str) -> dict:
        prof = self.profile()
        con = kb.connect(db_path)
        try:
            leg_row = query.leg_for(con, now)
            leg = self._leg(con, leg_row, prof)
            weather = self._weather_on_file(con, now)
        finally:
            con.close()
        chat = str(chat_id)
        loc = ss.get(AGENT, chat, "location", now=now, db_path=db_path)
        if loc and loc.get("lat") is not None:
            origin, origin_label = (loc["lat"], loc["lng"]), loc.get("label") or "where you are"
        elif loc:
            origin, origin_label = None, loc.get("label") or "where you are"
        elif leg.get("stay_lat") is not None:
            origin, origin_label = (leg["stay_lat"], leg["stay_lng"]), leg.get("stay_name") or "the stay"
        else:
            origin, origin_label = None, leg.get("stay_name") or ("home" if leg.get("kind") == "home" else None)
        overrides = [r["value"] | {"scope": r["scope"], "until": r["expires_at"]}
                     for r in ss.get_all(AGENT, chat, "override", now=now, db_path=db_path)]
        exclusions = [r["value"] | {"scope": r["scope"], "until": r["expires_at"]}
                      for r in ss.get_all(AGENT, chat, "exclusion", now=now, db_path=db_path)]
        return {
            "now": now, "chat_id": chat, "db_path": db_path, "trace_id": trace_id,
            "cassette_dir": cassette_dir or os.getenv("RAHAT_CASSETTE_DIR") or self.manifest.cassette_dir, "profile": prof,
            "leg": leg, "mode": "trip" if leg.get("kind") == "trip" else "home",
            "origin_latlng": origin, "origin_label": origin_label,
            "minutes_mode": leg.get("minutes_mode", "drive"),
            "speaker": self.speaker_for(chat), "diners": cprofile.diners_default(prof),
            "overrides": overrides, "exclusions": exclusions,
            "plan": ss.get(AGENT, chat, "plan", now=now, db_path=db_path),
            "last_answer": ss.get(AGENT, chat, "last_answer", now=now, db_path=db_path) or {},
            "weather": weather, "state_writes": [],
        }

    def _leg(self, con, row: dict | None, prof: dict) -> dict:
        home = prof.get("home") or {}
        if not row:
            return {"leg_id": "home", "kind": "home", "label": "home", "city": (home.get("city") or "").lower().replace(" ", "_"),
                    "tz": home.get("tz", "America/Los_Angeles"), "minutes_mode": home.get("minutes_mode", "drive"),
                    "radius_min": home.get("radius_min", 25), "stay_lat": home.get("lat"), "stay_lng": home.get("lng"),
                    "stay_name": "home"}
        leg = dict(row)
        leg["city"] = row["leg_id"] if row["kind"] == "trip" else (home.get("city") or "").lower().replace(" ", "_")
        leg["stay_lat"], leg["stay_lng"] = None, None
        if row.get("stay_location_id"):
            r = con.execute("SELECT lat, lng FROM kb_location WHERE location_id = ?", (row["stay_location_id"],)).fetchone()
            if r and r[0] is not None:
                leg["stay_lat"], leg["stay_lng"] = r[0], r[1]
        if row["kind"] == "home":
            leg["stay_lat"] = leg["stay_lat"] if leg["stay_lat"] is not None else home.get("lat")
            leg["stay_lng"] = leg["stay_lng"] if leg["stay_lng"] is not None else home.get("lng")
            leg["stay_name"] = leg.get("stay_name") or "home"
            leg["minutes_mode"] = leg.get("minutes_mode") or home.get("minutes_mode", "drive")
        try:
            leg["schedule"] = json.loads(row.get("schedule_json") or "{}")
        except (TypeError, ValueError):
            leg["schedule"] = {}
        return leg

    def _weather_on_file(self, con, now: datetime) -> dict | None:
        row = con.execute("SELECT fact_id, value_json, observed_at FROM kb_fact WHERE subject_type = 'weather' "
                          "ORDER BY observed_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        try:
            observed = datetime.fromisoformat(str(row[2])[:19])
        except ValueError:
            return None
        if now - observed > timedelta(hours=12):
            return None
        try:
            v = json.loads(row[1] or "{}")
        except ValueError:
            return None
        return {"fact_id": row[0], "issued": v.get("issued"), "hazards": v.get("hazards") or [], "observed_at": row[2]}

    # ── the reading, made consistent ─────────────────────────────────────
    _WHAT = ("meal", "cuisines", "dishes", "formats", "attributes", "reference_place")

    def refine_frame(self, frame: Frame, ctx: dict) -> tuple[Frame, list[str]]:
        """Deterministic rules on the model's reading.

        * A follow-up that refers to the last answer and says no "what" of
          its own ("other options please", "check across the whole area", "I
          can travel 60 minutes, expand the search") keeps the last ask's
          what, and its where unless it changes the where (outcome U7).
          LIVE 2026-10-04: every refinement of a coffee ask lost "coffee",
          "cozy", "a place to sit", and the searches came back empty.
        * A place named as a style reference ("nothing the style of Lantern House
          but cozy?") is not a verdict on it and not a standing exclusion:
          it is left out of this answer only. The same morning that read
          became a session-long exclusion of the very place being praised.
        """
        notes: list[str] = []
        upd: dict = {}
        last = (ctx.get("last_answer") or {}).get("frame") or {}
        has_what = any(getattr(frame, k) for k in self._WHAT)
        if frame.refers_to_last_answer and last and not has_what:
            for k in self._WHAT:
                if last.get(k):
                    upd[k] = last[k]
            if not frame.radius and not frame.area:
                for k in ("radius", "area"):
                    if last.get(k):
                        upd[k] = last[k]
            if upd:
                notes.append("kept from the last ask: " + ", ".join(sorted(upd)))
        ref = frame.reference_place
        if ref is None and upd.get("reference_place"):
            ref = ReferencePlace.model_validate(upd["reference_place"])
        if ref:
            target = kb.norm_name(ref.name)

            def same(name: str | None) -> bool:
                n = kb.norm_name(name or "")
                return bool(n) and (n == target or n in target or target in n)

            exclusions = []
            for e in frame.exclusions:
                if e.kind == "place" and same(e.value) and e.scope != "turn":
                    e = e.model_copy(update={"scope": "turn"})
                    notes.append(f"{ref.name}: a style reference is left out of this answer only")
                exclusions.append(e)
            verdicts = [v for v in frame.verdicts
                        if not (v.place and same(v.place) and v.kind in ("style", "rejected", "boring"))]
            if len(verdicts) != len(frame.verdicts):
                notes.append(f"{ref.name}: a style reference is not a verdict")
            upd["exclusions"], upd["verdicts"] = exclusions, verdicts
            if "eat" not in frame.intents:
                upd["intents"] = list(frame.intents) + ["eat"]
        if not upd:
            return frame, notes
        data = frame.model_dump()
        data.update(upd)
        return Frame.model_validate(data), notes

    # ── prompts ───────────────────────────────────────────────────────────
    def understand_prompt(self, text: str, ctx: dict, *, problems: list[str] | None = None) -> str:
        now = ctx["now"]
        last = ctx.get("last_answer") or {}
        session = {
            "now": now.strftime("%Y-%m-%d %H:%M (%A)"), "leg": {k: ctx["leg"].get(k) for k in ("leg_id", "kind", "label", "city", "tz")},
            "origin_label": ctx.get("origin_label"), "speaker": ctx.get("speaker"), "diners_default": ctx.get("diners"),
            "members": list((ctx.get("profile") or {}).get("members", {}).keys()),
            "overrides_in_force": ctx.get("overrides"), "exclusions_in_force": ctx.get("exclusions"),
            "plan_today": ctx.get("plan"),
            "last_answer": {"header": last.get("header"), "picks": [p.get("name") for p in last.get("picks", [])],
                            "kind": last.get("kind"), "when": last.get("at")} if last else None,
            "weather_hazards": (ctx.get("weather") or {}).get("hazards"),
        }
        parts = [
            "Read the message into the frame schema below. Rules: the speaker is a member id (the chat's role unless the "
            "message says otherwise, e.g. 'I'm the wife' → spouse); diners default to the household's usual party unless the "
            "message narrows it ('for my wife' → spouse only; 'just me' → the speaker); 'infant' is a diner id when the baby "
            "is along; a meal already eaten is history; the time is the one asked about (now, later, tomorrow, an arrival or "
            "departure time, 'open by'); the origin is where they are now, the area is where to look; a dictated or misspelled "
            "name goes in named_places as heard, with your best reading in dictation; exclusions carry a scope and any "
            "exception clause verbatim; verdicts carry their kind; a correction or 'something else' sets refers_to_last_answer; "
            "state every assumption; at most one question; confidence per field you were unsure about.",
            "Message: " + text,
            "Session: " + json.dumps(session, ensure_ascii=False, default=str),
            "Frame JSON Schema: " + json.dumps(frame_json_schema(), ensure_ascii=False),
            "Answer with only the JSON object.",
        ]
        if problems:
            parts.append("Your previous JSON did not validate. Fix every problem:\n- " + "\n- ".join(problems))
        return "\n\n".join(parts)

    def moment(self, ctx: dict, frame: Frame) -> dict:
        now = ctx["now"]
        last = ctx.get("last_answer") or {}
        return {
            "now": now.strftime("%Y-%m-%d %H:%M"), "weekday": now.strftime("%A"), "mode": ctx.get("mode"),
            "leg": {k: ctx["leg"].get(k) for k in ("leg_id", "kind", "label", "city", "tz", "minutes_mode", "radius_min", "stay_name")},
            "origin": ctx.get("origin_label"), "origin_known": ctx.get("origin_latlng") is not None,
            "diners": frame.diners or ctx.get("diners"), "call_them": self.labels(ctx, frame),
            "overrides_in_force": ctx.get("overrides"),
            "exclusions_in_force": ctx.get("exclusions"), "plan_today": ctx.get("plan"),
            "last_answer_picks": [p.get("name") for p in last.get("picks", [])],
            "last_answer_rejected": last.get("rejected", []),
            "weather_on_file": ctx.get("weather"),
            "brake": {"tool_calls": self.budgets.per_ask.tool_calls, "search_queries": self.budgets.per_ask.search_queries,
                      "usd": self.budgets.per_ask.usd},
        }

    def labels(self, ctx: dict, frame: Frame | None = None) -> dict[str, str]:
        """What a reply calls each diner ("you" for the speaker)."""
        diners = list((frame.diners if frame else None) or ctx.get("diners") or [])
        return cprofile.labels(ctx.get("profile") or {}, ctx.get("speaker"), diners)

    def rules_text(self, ctx: dict, frame: Frame) -> str:
        members = frame.diners or ctx.get("diners") or []
        members = [m for m in members if m != "infant"] + (["infant"] if "infant" in members else [])
        return cprofile.rules_text(ctx.get("profile") or {}, members)

    def household_rule_outcomes(self, ctx: dict) -> list[str]:
        """Standing household verdicts promote their outcomes to musts."""
        con = kb.connect(ctx.get("db_path"))
        try:
            kinds = {r[0] for r in con.execute("SELECT DISTINCT kind FROM kb_verdict WHERE scope = 'standing'")}
        finally:
            con.close()
        out = []
        for kind in kinds:
            out += {"chain": ["P9"], "price": ["P8"], "room": ["P7"], "reported_score": ["P10"]}.get(kind, [])
        return sorted(set(out))

    # ── tools, evidence, verification, rendering ───────────────────────────
    def registry(self, ctx: dict) -> ToolRegistry:
        return btools.build_registry(ctx, allow=self.manifest.tools)

    def evidence(self, ctx: dict) -> Evidence:
        return Evidence(kb.connect(ctx.get("db_path")), now=ctx["now"], mode=ctx.get("mode", "home"))

    def verify_context(self, frame: Frame, contract: Contract, answer: Answer, ctx: dict, evidence: Evidence) -> cverify.VerifyContext:
        return cverify.VerifyContext(
            frame=frame, contract=contract, answer=answer, evidence=evidence, now=ctx["now"], mode=ctx.get("mode", "home"),
            profile=ctx.get("profile") or {}, origin=ctx.get("origin_latlng"), origin_label=ctx.get("origin_label"),
            minutes_mode=ctx.get("minutes_mode", "drive"), state_writes=list(ctx.get("state_writes") or []),
            overrides=list(ctx.get("overrides") or []), exclusions_in_force=list(ctx.get("exclusions") or []),
            last_answer=ctx.get("last_answer") or None, banned_phrases=self.banned_phrases, leg=ctx.get("leg") or {})

    def render(self, frame: Frame, answer: Answer, evidence: Evidence, ctx: dict, disclosures: list[str]) -> hrender.Rendered:
        chains = ((ctx.get("profile") or {}).get("household") or {}).get("chains") or {}
        return hrender.render(frame, answer, evidence, now=ctx["now"], mode=ctx.get("mode", "home"),
                              origin=ctx.get("origin_latlng"), minutes_mode=ctx.get("minutes_mode", "drive"),
                              banned=self.banned_phrases, chains_label_from=int(chains.get("label_from_locations", 2)),
                              disclosures=disclosures, labels=self.labels(ctx, frame))

    # ── the floor ─────────────────────────────────────────────────────────
    def floor(self, text: str, ctx: dict) -> str:
        """Model down: meal and cuisine words only, answered from the
        knowledge base with the read dates shown."""
        kf = ccontrol.keyword_frame(text)
        con = kb.connect(ctx.get("db_path"))
        try:
            city = (ctx.get("leg") or {}).get("city")
            rows = query.places(con, city=city)
            excluded = query.excluded_ids(con, now=ctx["now"])
            saved = query.saved_ids(con)
            fams = {kb.CUISINE_FAMILIES.get(c, c) for c in kf["cuisines"]}
            out = []
            for p in rows:
                if p["place_id"] in excluded:
                    continue
                fam = (p.get("cuisine_family") or p.get("cuisine") or "").lower()
                if fams and fam not in fams and not any(c in (p.get("name") or "").lower() for c in kf["cuisines"]):
                    continue
                hours = kb.current_fact(con, "place", p["place_id"], "hours")
                rating = query.rating(con, p["place_id"])
                bits = [p.get("cuisine") or "", p.get("price_level") or ""]
                if rating:
                    bits.append(f"{rating['source']} {rating['value']} ({rating['count'] or '?'})")
                if hours:
                    hv = hours.get("value")
                    hv = hv.get("value") if isinstance(hv, dict) else hv
                    bits.append(f"hours on file: {hv} (read {str(hours.get('observed_at'))[:10]})")
                origin = ctx.get("origin_latlng")
                if origin and p.get("lat") is not None:
                    from core.kb import geo
                    bits.append(f"about {geo.about_minutes(origin[0], origin[1], p['lat'], p['lng'], mode=ctx.get('minutes_mode', 'drive'))} min")
                out.append((p["place_id"] in saved, p["name"], " · ".join(b for b in bits if b)))
            out.sort(key=lambda t: (not t[0], t[1]))
        finally:
            con.close()
        if not out:
            return "The model is down and my notes have nothing for that ask; try again in a few minutes."
        lines = ["From my notes (" + (kf["meal"] or "a meal") + (", " + "/".join(kf["cuisines"]) if kf["cuisines"] else "") + "):"]
        for saved_flag, name, bits in out[:3]:
            lines.append(f"• {hrender.md_escape(name)}{' (saved)' if saved_flag else ''} — {hrender.md_escape(bits)}")
        lines.append("Hours and status are as of the read dates above — call first.")
        return "\n".join(lines)

    # ── record ────────────────────────────────────────────────────────────
    def after_run(self, result: RunResult, ctx: dict) -> None:
        if result.frame is None:
            return
        chat = str(ctx.get("chat_id") or "")
        now = ctx["now"]
        last = ctx.get("last_answer") or {}
        rejected = list(last.get("rejected", []))
        for v in result.frame.verdicts:
            if v.kind in ("rejected", "chain", "closed", "price", "room", "reported_score") and v.place:
                for p in last.get("picks", []):
                    if kb.norm_name(v.place) in kb.norm_name(p.get("name", "")):
                        rejected.append(p.get("place_id"))
        picks = [{"place_id": p.place_id, "name": p.name} for p in (result.answer.picks if result.answer else [])]
        ss.put(AGENT, chat, "last_answer", {
            "trace_id": result.trace_id, "header": result.frame.header(), "kind": result.answer.kind if result.answer else None,
            "picks": picks, "rejected": sorted({r for r in rejected if r}), "at": now.strftime("%Y-%m-%d %H:%M"),
            "frame": result.frame.model_dump(exclude_none=True, exclude_defaults=True),
        }, scope="session", now=now, replace=True, source_trace=result.trace_id, db_path=ctx.get("db_path"))
        for e in result.frame.exclusions:
            if e.scope in ("session", "day", "trip") and not any(
                    x.get("value") == e.value and x.get("kind") == e.kind for x in ctx.get("exclusions") or []):
                ss.put(AGENT, chat, "exclusion", {"kind": e.kind, "value": e.value, "exception": e.exception},
                       scope=e.scope, now=now, source_trace=result.trace_id, db_path=ctx.get("db_path"))
        try:
            con = kb.connect(ctx.get("db_path"))
            try:
                con.execute("INSERT INTO kb_search (trace_id, agent, frame_json, candidate_ids, fact_ids, queries_run, cost_usd, "
                            "kb_hits, live_fetches, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (result.trace_id, AGENT, json.dumps(result.frame.model_dump(exclude_none=True, exclude_defaults=True)),
                             json.dumps([p["place_id"] for p in picks]), json.dumps(result.answer.evidence_ids() if result.answer else []),
                             result.search_queries, result.cost_usd, 0, 0, now.strftime("%Y-%m-%d %H:%M:%S")))
                con.commit()
            finally:
                con.close()
        except Exception:  # noqa: BLE001
            pass


_TENANT: BourdainTenant | None = None


def tenant() -> BourdainTenant:
    global _TENANT
    if _TENANT is None:
        _TENANT = BourdainTenant()
    return _TENANT


def reset() -> None:
    global _TENANT
    _TENANT = None
