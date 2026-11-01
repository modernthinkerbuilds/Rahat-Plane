"""Kipchoge bot core — transport-independent turn logic (testable).

Access model (PRD v0.3 §9): one athlete. KIPCHOGE_PRIMARY_CHAT is the
athlete's chat id (KIPCHOGE_ALLOWED_CHATS adds others, comma-separated);
any other chat gets one line and nothing from the store. No household,
no join code: the running coach talks to one person.

Modes (`agents.kipchoge.harness_bridge`, RAHAT_KIPCHOGE_HARNESS):
  off     code answers the grammar from records (the floors); free text
          gets the offline line.
  shadow  as off, and the harness runs each free-text ask in a thread,
          logging what it would have said (default outside tests).
  serve   the lookups by code; /next, /run, the exact replies and free
          text through the harness, the floor as the crash fallback.

Every turn: a decisions span (actor="kipchoge", op="kipchoge_bot.turn")
and the never-empty guard. The clock handed to the handler is the
athlete's home time (E5: America/Los_Angeles), not the Mac's.

On demand (owner, 2026-10-26: "it should have zero model calls every
day"): the coach calls the model only when the athlete asks. /run,
/next and /week in serve mode take the pipeline path (the records read
first, two rounds at most); nothing runs on a clock by default.

The minute tick (`tick_once`) never calls the model. KIPCHOGE_PROACTIVE
picks what it sends (owner, 2026-11-01):
  weekend (the default)  the next-run card on Friday and Saturday evenings
                         (`pipeline.weekend_card`); everything else on demand
  all (or 1)             the card the evening before every session, the
                         Sunday review (`pipeline.tick`), the final analysis
                         of a run that landed (`landed`), the provisional one
                         from the Watch's minute data (`provisional`)
  off (or 0)             nothing on a clock
All from the templates; each sends at most once per run per stage (the
`kipchoge_sent` table).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

HOME_ZONE = "America/Los_Angeles"
LANDED_WINDOW_H = 48          # a run older than this is history, not news (no flood on first boot)
PROVISIONAL_SETTLE_MIN = 10   # the dense heart-rate stretch must have ended this long ago: the run is over
RUN_LIKE_HR_MARGIN = 10       # on a planned run day, a stretch at zone2_lo − this or above is provisionally the run

_NOT_YOURS = ("Hi — I'm Kipchoge, a private running coach. This bot talks to one athlete, "
              "and this chat isn't paired with it.")
_FALLBACK = "Sorry — I couldn't put that together just now. Try again in a moment, or /help."
OFFLINE = ("The coach's free-text answers aren't switched on yet. From the store today: "
           "/next, /run, /me, /zones, /goal, /trend, /week, /routes.")
HELP = (
    "I'm Kipchoge, your running coach. Every number I give comes from the store.\n"
    "/next — the next planned run: minutes, heart-rate band, cap, expected pace\n"
    "/run — the last run's analysis (/run list, /run 2026-10-18, /run yesterday)\n"
    "/week — this week's sessions against the plan\n"
    "/trend — the rolling pace at the reference heart rate, and the goal forecast\n"
    "/goal — the goal in force\n"
    "/zones — your heart-rate bands\n"
    "/me — the athlete file\n"
    "/routes — the routes on file\n"
    "After a card: done · skip · moved. Or just ask."
)
HOLDING = "Reading the run — a minute."
_ACKS = {"done": "Noted: done.", "skip": "Noted: skipped.", "moved": "Noted: moved.", "go": "Go.",
         "yes": "Noted.", "no": "Noted.", "more": "That's all I have from the store for now."}
_NEXT_WORDS = ("next run", "tomorrow", "target for my next", "what should i run")
_RUN_WORDS = ("analy", "how was", "my run", "last run", "that run")


# ── access, clock ─────────────────────────────────────────────────────
def _primary_chat() -> str:
    return (os.getenv("KIPCHOGE_PRIMARY_CHAT") or "").strip()


def allowed_chats() -> set[str]:
    out = {c.strip() for c in (os.getenv("KIPCHOGE_ALLOWED_CHATS") or "").split(",") if c.strip()}
    if _primary_chat():
        out.add(_primary_chat())
    return out


def is_allowed(chat_id: str | int) -> bool:
    return str(chat_id) in allowed_chats()


def home_now(now_utc: datetime | None = None) -> datetime:
    """Naive local time in the athlete's home zone (E5); the machine clock
    when the zone database is missing."""
    utc = now_utc.astimezone(timezone.utc) if (now_utc and now_utc.tzinfo) else (now_utc.replace(tzinfo=timezone.utc) if now_utc else datetime.now(timezone.utc))
    try:
        from zoneinfo import ZoneInfo
        return utc.astimezone(ZoneInfo(HOME_ZONE)).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return utc.astimezone().replace(tzinfo=None)


# ── one turn ──────────────────────────────────────────────────────────
def process_message(chat_id: str | int, text: str, now: datetime | None = None, *,
                    send: Callable[[str], None] | None = None, db_path: str | None = None,
                    trusted: bool = False) -> str:
    """One inbound message → one reply. Never raises, never empty.
    `send` lets the harness post its holding line while it works.
    `trusted` is Miya's route: the sender was gated there, so the
    coach's own chat allow-list is not consulted."""
    from core import decisions
    cid = str(chat_id)
    text = (text or "").strip()
    tid = decisions.new_trace()
    try:
        with decisions.span("kipchoge_bot.turn", trace_id=tid, actor="kipchoge", input=text[:200]) as s:
            reply = _process(cid, text, now or home_now(), send=send, db_path=db_path, trusted=trusted)
            s.output = (reply or "")[:200]
    except Exception as e:  # noqa: BLE001 — the poll loop must survive
        logger.exception("kipchoge_bot turn failed: %s", e)
        reply = _FALLBACK
    if not (reply or "").strip():
        logger.warning("empty kipchoge reply — substituting fallback")
        reply = _FALLBACK
    return reply


def _process(cid: str, text: str, now: datetime, send: Callable[[str], None] | None, db_path: str | None,
             trusted: bool = False) -> str:
    from agents.kipchoge import harness_bridge as hb
    from core import inbound
    if not trusted and not is_allowed(cid):
        logger.warning("message from an unpaired chat %s", cid)
        return _NOT_YOURS
    if not text:
        return HELP
    mode = hb.mode()
    hit = inbound.match(text, hb.grammar())
    if hit and hit.kind == "command":
        cmd, args = hit.command, hit.args
        if cmd in ("/start", "/help"):
            return HELP
        if cmd == "/me":
            return _lookup(cid, now, db_path, me_text)
        if cmd == "/zones":
            return _lookup(cid, now, db_path, zones_text)
        if cmd == "/goal":
            return _lookup(cid, now, db_path, goal_text)
        if cmd == "/trend":
            return _lookup(cid, now, db_path, trend_text)
        if cmd == "/routes":
            return _lookup(cid, now, db_path, routes_text)
        if cmd == "/week":
            return _on_demand(cid, now, db_path, mode, send, lambda ctx, m: week_text(ctx, model=m, db_path=db_path))
        if cmd == "/next":
            return _on_demand(cid, now, db_path, mode, send, lambda ctx, m: next_text(ctx, model=m, db_path=db_path))
        if cmd == "/run":
            model_ok = args.strip().lower() != "list"
            return _on_demand(cid, now, db_path, mode if model_ok else "off", send,
                              lambda ctx, m: run_text(ctx, args, model=m, db_path=db_path))
        return HELP
    if hit and hit.kind == "exact_reply":
        if mode == "serve":
            return hb.answer(hit.command, chat_id=cid, now=now, holding_line=send, db_path=db_path, fallback=lambda: _ACKS[hit.command])
        return _ACKS.get(hit.command, "Noted.")
    if inbound.is_command_shaped(text):          # a slash command the manifest doesn't declare: the list, not the model
        return HELP
    if mode == "serve":
        return hb.answer(text, chat_id=cid, now=now, holding_line=send, db_path=db_path,
                         fallback=lambda: _offline_text(cid, text, now, db_path))
    reply = _offline_text(cid, text, now, db_path)
    if mode == "shadow":
        hb.shadow(text, chat_id=cid, now=now, code_reply=reply, db_path=db_path)
    return reply


def _on_demand(cid: str, now: datetime, db_path: str | None, mode: str, send: Callable[[str], None] | None,
               fn: Callable[[dict, bool], str | None]) -> str:
    """A command the athlete asked for: the model's version through the
    pipeline path in serve mode (the records read first; the holding line
    while it works), the template otherwise or when the model fails."""
    if mode == "serve":
        try:
            if send is not None:
                try:
                    send(HOLDING)
                except Exception:  # noqa: BLE001 - a failed holding line never fails the ask
                    pass
            text = _lookup(cid, now, db_path, lambda ctx: fn(ctx, True))
            if text and text.strip():
                return text
        except Exception as e:  # noqa: BLE001 - the template answers
            logger.exception("on-demand model path failed: %s", e)
    return _lookup(cid, now, db_path, lambda ctx: fn(ctx, False))


def _offline_text(cid: str, text: str, now: datetime, db_path: str | None) -> str:
    """Free text with the harness off: the card or the analysis when the
    words ask for one, the offline line otherwise."""
    low = text.lower()
    if any(w in low for w in _NEXT_WORDS):
        return _lookup(cid, now, db_path, next_text)
    if any(w in low for w in _RUN_WORDS):
        return _lookup(cid, now, db_path, lambda ctx: run_text(ctx, ""))
    return OFFLINE


# ── the lookups: code from records ────────────────────────────────────
def _lookup(cid: str, now: datetime, db_path: str | None, fn: Callable[[dict], str | None]) -> str:
    from agents.kipchoge import tenant as kt
    ctx = kt.tenant().build_ctx(cid, now, "", db_path=db_path, cassette_dir=None, trace_id="bot")
    try:
        return fn(ctx) or "Nothing on file for that yet."
    finally:
        con = ctx.get("con")
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass


def _tool(ctx: dict, name: str, args: dict) -> None:
    from agents.kipchoge import tools as ktools
    ktools.build_registry(ctx).execute(name, args, ctx)


def me_text(ctx: dict) -> str:
    from agents.kipchoge import render as krender
    _tool(ctx, "get_athlete", {})
    return krender.athlete_floor(ctx["evidence"], units=krender._units(ctx))


def zones_text(ctx: dict) -> str:
    from agents.kipchoge import render as krender
    _tool(ctx, "get_athlete", {})
    return krender.zones_floor(ctx["evidence"], units=krender._units(ctx))


def goal_text(ctx: dict) -> str:
    from agents.kipchoge import render as krender
    _tool(ctx, "get_goal", {})
    try:
        _tool(ctx, "trend", {})
    except Exception:  # noqa: BLE001 — no runs yet: the goal without a forecast
        pass
    return krender.goal_floor(ctx["evidence"], units=krender._units(ctx))


def trend_text(ctx: dict) -> str:
    from agents.kipchoge import render as krender
    try:
        _tool(ctx, "trend", {})
    except Exception as e:  # noqa: BLE001
        logger.info("trend unavailable: %s", e)
    return krender.trend_floor(ctx["evidence"], units=krender._units(ctx))


def routes_text(ctx: dict) -> str:
    from agents.kipchoge import render as krender
    for key in ((ctx.get("routes") or {}).get("routes") or {}):
        _tool(ctx, "route_info", {"name": key})
    return krender.routes_floor(ctx["evidence"], units=krender._units(ctx))


def _tenant():
    from agents.kipchoge import tenant as kt
    return kt.tenant()


def week_text(ctx: dict, *, model: bool = False, db_path: str | None = None) -> str | None:
    from agents.kipchoge import pipeline
    return pipeline.review_text(ctx, now=ctx["now"], model=model, tenant=_tenant(), db_path=db_path) or "No plan covers this week."


def next_text(ctx: dict, *, model: bool = False, db_path: str | None = None) -> str | None:
    from agents.kipchoge import pipeline
    return pipeline.card_text(ctx, now=ctx["now"], model=model, tenant=_tenant(), db_path=db_path) or "Nothing planned in the next seven days."


def run_text(ctx: dict, args: str, *, model: bool = False, db_path: str | None = None) -> str | None:
    """`/run` → the last run's analysis; `/run list` → the recent runs;
    `/run <date>` / `/run yesterday` → that day's run."""
    from agents.kipchoge import ingest, pipeline
    from agents.kipchoge import render as krender
    arg = (args or "").strip().lower()
    if arg == "list":
        _tool(ctx, "list_runs", {"n": 8})
        return krender.runs_floor(ctx["evidence"], units=krender._units(ctx))
    now = ctx["now"]
    if arg and arg != "last":
        day = (now - timedelta(days=1)).strftime("%Y-%m-%d") if arg == "yesterday" else now.strftime("%Y-%m-%d") if arg == "today" else arg
        con = ctx["con"]
        ids = ingest.runs_in(con, (now - timedelta(days=400)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
        for wid in reversed(ids):
            row = ingest.summary_row(con, wid)
            if row and str(row["start"]).startswith(day):
                return pipeline.analysis_text(ctx, wid, now=now, model=model, tenant=_tenant(), db_path=db_path)
        return f"No run on {day} in the store."
    return pipeline.last_analysis_text(ctx, now=now, model=model, tenant=_tenant(), db_path=db_path) or "No runs in the store yet."


# ── the minute tick ───────────────────────────────────────────────────
def checkin_mode() -> str:
    """"weekend" unless KIPCHOGE_PROACTIVE says "all" (1, true, on, yes) or "off" (0, false, no, none)."""
    v = (os.getenv("KIPCHOGE_PROACTIVE") or "").strip().lower()
    if v in ("1", "true", "on", "yes", "all"):
        return "all"
    if v in ("0", "false", "off", "no", "none"):
        return "off"
    return "weekend"


def proactive_enabled() -> bool:
    """Anything on a clock at all (the weekend cards by default)."""
    return checkin_mode() != "off"


def tick_once(now: datetime, *, send: Callable[[str], Any] | None, db_path: str | None = None) -> list[dict]:
    """What this minute sends. By default (weekend) the next-run card on
    Friday and Saturday evenings and nothing else; with "all", the
    evening card and the Sunday review (`pipeline.tick`), the final
    analysis of a run that landed, the provisional analysis of a run the
    Watch shows. Templates only: the tick never calls the model, in any
    mode (owner, 2026-10-26: zero model calls a day unless he asks).
    Never raises."""
    from agents.kipchoge import pipeline
    from agents.kipchoge import tenant as kt
    mode = checkin_mode()
    if mode == "off":
        return []
    tenant = kt.tenant()
    if mode == "weekend":
        try:
            return pipeline.weekend_card(tenant, now=now, send=send, db_path=db_path)
        except Exception as e:  # noqa: BLE001 — the tick never raises
            logger.exception("weekend card failed: %s", e)
            return []
    use_model = False
    out: list[dict] = []
    for step in (lambda: pipeline.tick(tenant, now=now, send=send, db_path=db_path, model=use_model),
                 lambda: landed(tenant, now, send=send, db_path=db_path, model=use_model),
                 lambda: provisional(tenant, now, send=send, db_path=db_path, model=use_model)):
        try:
            out += step() or []
        except Exception as e:  # noqa: BLE001 — one failing step never stops the others
            logger.exception("tick step failed: %s", e)
    return out


def landed_runs(con, now: datetime, *, window_h: int = LANDED_WINDOW_H) -> list[str]:
    """Primary runs that started within the window and have no final analysis sent."""
    from agents.kipchoge import ingest, pipeline
    pipeline.ensure_schema(con)
    since = (now - timedelta(hours=window_h)).strftime("%Y-%m-%d")
    ids = ingest.runs_in(con, since, (now + timedelta(days=1)).strftime("%Y-%m-%d"))
    out = []
    for wid in ids:
        row = ingest.summary_row(con, wid)
        start = ingest._dt(row["start"]) if row else None
        if start is None or (now - start) > timedelta(hours=window_h) or start > now + timedelta(hours=1):
            continue
        if pipeline.already_sent(con, f"{wid}:final") is None:
            out.append(wid)
    return out


def landed(tenant: Any, now: datetime, *, send: Callable[[str], Any] | None, db_path: str | None = None,
           model: bool = False) -> list[dict]:
    from agents.kipchoge import pipeline, state
    con = state.connect(db_path or state.db_path())
    try:
        wids = landed_runs(con, now)
    finally:
        con.close()
    out = []
    for wid in wids:
        res = pipeline.on_workout_landed(tenant, wid, now=now, send=send, db_path=db_path, model=model)
        res["kind"], res["workout_id"] = "analysis", wid
        out.append(res)
    return out


def _vitals_sessions(now: datetime) -> list[dict]:
    """Today's sessions from the Watch's minute data (Huberman's detectors)."""
    from agents.huberman import context as hctx
    return hctx.sessions_from_vitals(now)


def provisional_candidates(sessions: list[dict], *, now: datetime, plan: dict, zones: dict) -> list[dict]:
    """Which of today's detected sessions is provisionally a run: one the
    running-form series shows (the Watch writes it only inside a running
    workout), or, on a planned run day, a dense heart-rate stretch at
    running heart rates. The stretch must have ended (the run is over)."""
    from agents.kipchoge import ingest, pipeline
    from agents.kipchoge import plan as cplan
    settled = now - timedelta(minutes=PROVISIONAL_SETTLE_MIN)
    planned_today = cplan.session_on(plan or {}, now.strftime("%Y-%m-%d")) is not None
    floor_hr = (zones or {}).get("zone2_lo")
    out = []
    for s in sessions:
        end = ingest._dt(s.get("end"))
        if end is None or end > settled or int(s.get("minutes") or 0) < pipeline.PROVISIONAL_MIN_MINUTES:
            continue
        if s.get("kind") == "run" or "run" in str(s.get("name") or "").lower():
            out.append(s)
        elif planned_today and floor_hr and (s.get("avg_hr") or 0) >= floor_hr - RUN_LIKE_HR_MARGIN:
            out.append(s)
    return out


def provisional(tenant: Any, now: datetime, *, send: Callable[[str], Any] | None, db_path: str | None = None,
                model: bool = False) -> list[dict]:
    from agents.kipchoge import ingest, pipeline, state
    sessions = _vitals_sessions(now)
    if not sessions:
        return []
    zones = state.zones(tenant.athlete())
    candidates = provisional_candidates(sessions, now=now, plan=tenant.plan(), zones=zones)
    if not candidates:
        return []
    con = state.connect(db_path or state.db_path())
    try:
        pipeline.ensure_schema(con)
        fresh = [s for s in candidates if pipeline.already_sent(con, f"{ingest.provisional_id(s)}:provisional") is None]
    finally:
        con.close()
    out = []
    for s in fresh:
        res = pipeline.on_provisional_run(tenant, s, now=now, send=send, db_path=db_path, model=model)
        res["kind"], res["start"] = "analysis", s.get("start")
        out.append(res)
    return out
