"""agents.huberman.protocols — the deterministic mobility drill library.

Huberman S1 (2026-08-24, owner: "lets build huberman now … I want
huberman at 9:30 pm autorun … give me a cool down … ideally start
with give 15 min including transition time").

This module is the NEVER-EMPTY floor under the LLM coach: a typed
library of cooldown / down-regulation drills plus a deterministic
composer that can always assemble a time-boxed session from it — no
API key, no budget, no network. coach.py prefers the LLM voice; when
that path errors, this one answers.

Design rules encoded here:
  * VARIETY IS A REQUIREMENT, not a nicety. The owner's explicit
    complaint about the previous coach: it repeated the same drills
    every day. compose() takes `exclude` (recently-used drill keys,
    from state.recent_drills) and rotates deterministically.
  * INJURY TAGS ARE HARD FILTERS. A drill tagged with a contraindication
    the profile currently carries (e.g. "trochanter_compression" while
    the right-hip GTPS episode finishes resolving) is NEVER emitted,
    LLM or fallback. Tendon-friendly isometrics/stretch stay available.
  * Times INCLUDE transitions. Each drill's `minutes` budgets setup +
    the work, so the rendered session honestly fits the requested slot.
  * Every session ENDS with a down-regulation breathing drill — this is
    a 9:30 PM protocol; the last thing it does is downshift the nervous
    system for sleep (trap/neck release + long-exhale, the same
    guardrail Fraser's Part 6 encodes).
  * MOTOR CONTROL IS A TIER (2026-09-12). A hands-on assessment can
    conclude that length is NOT the limiter — that a joint runs out of
    range because the pattern is wrong, not because the tissue is
    short. For that athlete passive holds are the wrong tool and the
    library must offer the replacement: kind="control" drills (PNF,
    hinge rehearsal, bridges with a top squeeze) that re-teach the
    joint where to go. The profile decides which passive classes are
    retired (`avoid_tags`: passive_hamstring, passive_er, anterior_band)
    and how many control drills a session carries
    (`preferences.control_drills`); the library stays generic.

PII note: this file ships in the PUBLIC repo. Drill definitions are
generic coaching content; everything athlete-specific (hotspots,
equipment owned, active issues) lives in vault/huberman_profile.json
and is applied at compose time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Drill:
    key: str
    name: str
    minutes: float                    # includes setup/transition time
    kind: str                         # "soft_tissue" | "stretch" | "control" | "downreg"
    areas: tuple[str, ...]            # body areas served
    cue: str                          # the one coaching cue that matters
    equipment: tuple[str, ...] = ()   # required kit ('' entries = none)
    contra: tuple[str, ...] = ()      # contraindication tags
    rationale: str = ""               # built-in "why" (control drills:
                                      # the pattern they re-teach)
    sided: bool = True                # done one side at a time (a side
                                      # bias can dose it); False = bilateral


# ── The library ────────────────────────────────────────────────────────
# Areas use a small controlled vocabulary so profile hotspots can match:
#   neck, traps, t_spine, lats, glutes, hip, hamstrings, quads, calves,
#   foot, full_body
DRILLS: tuple[Drill, ...] = (
    # — soft tissue —
    Drill("peanut_suboccipital", "Peanut ball — suboccipital release", 3.0,
          "soft_tissue", ("neck",),
          "Lie on the peanut at the skull base; chin nods only, slow.",
          ("peanut ball",)),
    Drill("peanut_tspine", "Peanut ball — T-spine extension ladder", 3.5,
          "soft_tissue", ("t_spine",),
          "Two breaths per segment, arms overhead on the exhale.",
          ("peanut ball",)),
    Drill("lax_trap_wall", "Lacrosse ball — trap/levator against wall", 3.0,
          "soft_tissue", ("traps", "neck"),
          "Pin the knot, then slowly turn the head away and nod.",
          ("lacrosse ball",)),
    Drill("lax_foot_arch", "Lacrosse ball — plantar arch roll", 2.5,
          "soft_tissue", ("foot",),
          "Slow passes heel to ball of foot; pause on the hot spots.",
          ("lacrosse ball",)),
    Drill("lax_glute", "Lacrosse ball — glute/deep rotator search", 3.0,
          "soft_tissue", ("glutes", "hip"),
          "Sit on the ball, cross the ankle over; move slow, breathe.",
          ("lacrosse ball",),
          ("trochanter_compression",)),      # direct lateral-hip pressure
    Drill("roller_quads", "Foam roller — quads slow flush", 3.0,
          "soft_tissue", ("quads",),
          "Nose-breathing pace only; bend/extend the knee on tender spots.",
          ("foam roller",)),
    Drill("roller_lats", "Foam roller — lat sweep", 2.5,
          "soft_tissue", ("lats", "t_spine"),
          "Side-lying, thumb up; rock, don't race.",
          ("foam roller",)),
    Drill("roller_calves", "Foam roller — calves stack", 2.5,
          "soft_tissue", ("calves", "foot"),
          "Stack one leg, ankle circles under pressure.",
          ("foam roller",)),
    # — stretch / capsule —
    Drill("couch_stretch", "Couch stretch", 4.0,
          "stretch", ("hip", "quads"),
          "Squeeze the glute of the down leg; tall spine, no lumbar arch.",
          ()),
    Drill("pigeon", "Pigeon — front-leg bias", 4.0,
          "stretch", ("glutes", "hip"),
          "Square the hips, long exhale into the front glute.",
          (),
          ("passive_er",)),                  # deep passive external rotation
    Drill("ninety_ninety", "90/90 hip switches → hold", 3.5,
          "stretch", ("hip", "glutes"),
          "Slow switches x5, then 60s hold each side, chest proud.",
          (),
          ("passive_er",)),
    Drill("band_hip_distraction",
          "Banded hip distraction (anchor in front, traction)", 4.0,
          "stretch", ("hip",),
          "Band high in the capsule, anchor in front, sit back; "
          "joint-friendly on a grumpy lateral hip — traction, not "
          "compression.",
          ("band", "door anchor strap"),
          ("anterior_band",)),               # pulls the femur FORWARD
    Drill("band_posterior_glide",
          "Banded posterior hip glide (quadruped, anchor behind)", 4.0,
          "control", ("hip", "glutes"),   # an ACTIVE seat-the-femur
                                          # mobilization, so it rotates
                                          # with the hinge and the bridge
          "Band high on the thigh pulling BACK toward the anchor; "
          "quadruped, sit the hips back and let the band seat the "
          "femur in the socket. Long exhale, no lumbar rounding.",
          ("band", "door anchor strap"),
          (),
          "seats the femoral head back in the socket — the posterior "
          "glide a forward-sitting hip is missing"),
    Drill("band_hamstring", "Banded hamstring PNF (contract-relax)", 3.5,
          "stretch", ("hamstrings",),
          "Leg vertical, band on the arch: push INTO the band 5s at "
          "~30 percent, relax, take the new range; 4 rounds a side.",
          ("band",),
          (),
          "active contract-relax instead of a passive hold — length "
          "gains you can keep"),
    Drill("band_neck_stretch", "Banded first-rib / scalene opener", 3.0,
          "stretch", ("neck", "traps"),
          "Band over the trap, step away; long neck, ear to shoulder.",
          ("band",)),
    Drill("posttib_stretch", "Post-tib / ankle dorsiflexion wall lean", 3.0,
          "stretch", ("foot", "calves"),
          "Knee tracks over the pinky toe; heel glued down.",
          ()),
    Drill("db_farmer_hang", "DB anchor stretch — weighted forward fold", 3.0,
          "stretch", ("hamstrings", "full_body"),
          "Hold the DBs, hinge, let the weight lengthen the fold — "
          "zero pulling.",
          ("dumbbells",),
          ("passive_hamstring",)),           # a loaded passive hold
    Drill("tspine_wall_reach", "Wall thoracic reach-backs", 3.0,
          "stretch", ("t_spine", "lats"),
          "Hips back, arms on the wall; drop the chest through.",
          ()),
    Drill("glute_isometric", "Side-lying hip abduction isometrics", 3.0,
          "stretch", ("hip", "glutes"),
          "Tendon-friendly loading for a resolving lateral hip: "
          "5 x 20s holds at 50 percent effort, zero pain rule.",
          ()),
    # — motor control (re-teach the pattern; never passive) —
    Drill("dowel_hinge", "Dowel hinge rehearsal", 3.5,
          "control", ("hip", "hamstrings"),
          "Dowel on head, T-spine and sacrum. Push the hips BACK, soft "
          "knees, chest long; stop the instant the dowel leaves any "
          "point. 3 x 8 slow reps.",
          (),
          (),
          "re-teaches a hip-first hinge — hips travel back and the "
          "spine stays long instead of rounding to reach the floor",
          sided=False),
    Drill("glute_bridge_squeeze", "Glute bridge — 3s top squeeze", 3.0,
          "control", ("glutes", "hip"),
          "Heels close, ribs down; drive through the heels and SQUEEZE "
          "at the top for 3s before the hamstrings can take over. "
          "2 x 10, then 8 single-leg on the weaker side.",
          (),
          (),
          "wakes the glutes to own hip extension so the hamstrings "
          "stop doing all of it"),
    # — down-regulation (closers) —
    Drill("breath_48", "4-8 breathing", 3.0,
          "downreg", ("full_body",),
          "Inhale 4 through the nose, exhale 8 through pursed lips.",
          ()),
    Drill("box_breath", "Box breathing 4-4-4-4", 3.0,
          "downreg", ("full_body",),
          "Equal sides; soften the jaw and the gaze.",
          ()),
    Drill("phys_sigh", "Physiological sighs, then slow nasal", 2.5,
          "downreg", ("full_body",),
          "Double inhale, long sighing exhale x5; then 2 min quiet "
          "nasal breathing.",
          ()),
    Drill("legs_up_wall", "Legs up the wall + slow exhale", 4.0,
          "downreg", ("full_body", "hamstrings"),
          "Hips near the wall, arms wide; exhale longer than you inhale.",
          ()),
)

_BY_KEY = {d.key: d for d in DRILLS}


# ── Movement → tissue stress (2026-09-03) ──────────────────────────────
# Owner: "have this explain why it made a certain stretch choice —
# based on today's workout." The programmed WOD text (SugarWOD, via
# Kobe) is scanned for movement families; each hit names the areas it
# loaded and a one-line reason the coach can quote. Areas use the
# drill vocabulary above so a hit maps straight onto drill selection,
# and the reason becomes the drill's "why" tail. Regexes are loose on
# purpose (SugarWOD spelling varies: "Pull-Up", "pullups", "C2B").
MOVEMENT_STRESS: tuple[tuple[re.Pattern, tuple[str, ...], str], ...] = (
    (re.compile(r"\b(?:power\s+|hang\s+|squat\s+)?(?:cleans?|snatch(?:es)?|"
                r"jerks?)\b", re.I),
     ("hip", "t_spine", "traps"),
     "cleans/snatches load the hip drive, the rack position and the "
     "upper traps"),
    (re.compile(r"\b(?:front|back|overhead|goblet)?\s*squats?\b|"
                r"\bthrusters?\b|\bwall[\s-]?balls?\b", re.I),
     ("hip", "glutes", "quads"),
     "squatting under load compresses the hip capsule — the glute-crease "
     "catch lives exactly there"),
    (re.compile(r"\bdead\s*lifts?\b|\b(?:kb|kettlebell)\s+swings?\b|"
                r"\bgood\s+mornings?\b|\bRDLs?\b", re.I),
     ("hamstrings", "glutes"),
     "hinge volume shortens the hamstrings and hammers the glute origin"),
    (re.compile(r"\b(?:strict\s+|kipping\s+)?(?:pull|chin)[\s-]?ups?\b|"
                r"\bmuscle[\s-]?ups?\b|\bring\s+rows?\b|"
                r"\brope\s+climbs?\b|\btoes[\s-]to[\s-]bar\b|\bC2B\b|"
                r"\bT2B\b", re.I),
     ("lats", "t_spine"),
     "pulling volume locks up the lats and mid-back"),
    (re.compile(r"\bbox\s+jumps?\b|\bdouble[\s-]?unders?\b|"
                r"\bjump\s+rope\b|\bburpees?\b|\brun(?:ning)?\b|"
                r"\bsprints?\b", re.I),
     ("calves", "foot"),
     "jumping/running loads the calves and the post-tib line under the "
     "right arch"),
    (re.compile(r"\b(?:push|shoulder|strict)\s+press\b|\bpress(?:es)?\b|"
                r"\bHSPU\b|\bhandstands?\b|\boverhead\b|"
                r"\bpush[\s-]?ups?\b|\bbench\b|\bdips?\b", re.I),
     ("neck", "traps", "t_spine"),
     "overhead/pressing volume is the known trigger for the right "
     "cervical spine"),
    (re.compile(r"\brow(?:ing|er)?\b|\bcal(?:orie)?s?\s+(?:row|bike)\b|"
                r"\b(?:assault|echo|air)\s*bike\b|\bski\s*erg\b", re.I),
     ("hip", "t_spine"),
     "erg work rounds the mid-back and shortens the hip flexors"),
    (re.compile(r"\blunges?\b|\bstep[\s-]?ups?\b|\bpistols?\b", re.I),
     ("hip", "quads", "glutes"),
     "single-leg work loads the hip stabilizers and quads unevenly"),
)


def loaded_areas(wod_text: str | None) -> list[tuple[str, tuple[str, ...], str]]:
    """Scan a WOD's text → [(movement_hit, areas, reason)] in first-seen
    order, one entry per movement family. Empty when nothing matches."""
    out = []
    seen: set[int] = set()
    for i, (rx, areas, why) in enumerate(MOVEMENT_STRESS):
        m = rx.search(wod_text or "")
        if m and i not in seen:
            seen.add(i)
            out.append((m.group(0), areas, why))
    return out


def why_for(drill: Drill, loads: list[tuple[str, tuple[str, ...], str]],
            hotspots: list[dict]) -> str:
    """The one-line reason a drill earned its slot: the first WOD
    movement whose loaded areas it serves; else the hotspot it
    maintains; else its role as the down-regulation closer. A drill
    with a built-in rationale (control work, the posterior glide)
    leads with the movement hit when there is one and always ends on
    the pattern it re-teaches — that IS the reason it was chosen."""
    for hit, areas, why in loads:
        if set(areas) & set(drill.areas):
            if drill.rationale:
                return f"{hit.lower()} — {drill.rationale}"
            return f"{hit.lower()} — {why}"
    if drill.rationale:
        return drill.rationale
    for h in hotspots:
        if h.get("area_tag") in drill.areas:
            return f"maintenance on a known hotspot ({h.get('label') or h.get('area_tag')})"
    if drill.kind == "downreg":
        return "closes the session — long exhales flip you toward sleep"
    return "general recovery of what today loaded"


def drill(key: str) -> Drill:
    return _BY_KEY[key]


def known_keys() -> frozenset[str]:
    return frozenset(_BY_KEY)


def _equipment_ok(d: Drill, owned: list[str]) -> bool:
    """A drill is available if every required item matches something the
    athlete owns. Match = every WORD of the requirement appears in one
    owned item, any order ('door anchor strap' matches 'door hip anchor
    strap'; 'band' matches 'green Rogue band'). The 2026-09-12 audit
    found the owner's profile had never qualified for a single
    door-anchor band drill because of the old substring rule."""
    if not d.equipment:
        return True
    owned_words = [set(re.findall(r"[a-z0-9]+", o.lower())) for o in owned]
    for req in d.equipment:
        need = set(re.findall(r"[a-z0-9]+", req.lower()))
        if not need:
            continue
        if not any(need <= have for have in owned_words):
            return False
    return True


def eligible(profile: dict) -> list[Drill]:
    """Filter the library by the athlete's equipment + contraindications.
    Contra tags come from profile['avoid_tags'] (state.load_profile
    derives them from active issues)."""
    owned = profile.get("equipment") or []
    avoid = set(profile.get("avoid_tags") or [])
    out = []
    for d in DRILLS:
        if avoid & set(d.contra):
            continue
        if owned and not _equipment_ok(d, owned):
            continue
        if not owned and d.equipment:
            continue                     # no kit on file → bodyweight only
        out.append(d)
    return out


def compose(minutes: float, profile: dict,
            exclude: set[str] | None = None,
            focus: list[str] | None = None,
            salt: int = 0) -> list[Drill]:
    """Deterministically assemble a session of ~`minutes` total.

    Selection order: (1) soft tissue on hotspot areas, (2) motor-control
    work on those areas (`preferences.control_drills` picks, default 1),
    (3) stretch on hotspot then focus areas, (4) general stretch filler,
    (5) ALWAYS one down-regulation closer. `exclude` drops recently-used
    keys (variety rule) unless that would empty a tier — never
    sacrifice the session to the rotation. `salt` rotates
    equally-ranked picks so two calls on the same day still differ.
    """
    exclude = exclude or set()
    hot = [h.get("area_tag") or "" for h in (profile.get("hotspots") or [])]
    want = [a for a in (focus or []) if a] or hot or ["hip", "t_spine"]
    prefs = profile.get("preferences") or {}
    try:
        control_cap = max(0, int(prefs.get("control_drills", 1)))
    except (TypeError, ValueError):
        control_cap = 1

    pool = eligible(profile)

    def _tier(kind: str, areas: list[str] | None,
              strict: bool = False) -> list[Drill]:
        t = [d for d in pool if d.kind == kind
             and (areas is None or any(a in d.areas for a in areas))]
        fresh = [d for d in t if d.key not in exclude]
        # Rotation never empties a REQUIRED tier; the optional control
        # tier is strict, so a one-drill tier can't repeat every night.
        t = fresh if strict else (fresh or t)
        # Stable rotation: rank by key, rotate by salt.
        t = sorted(t, key=lambda d: d.key)
        off = salt % len(t) if t else 0
        return t[off:] + t[:off]

    closers = _tier("downreg", None)
    closer = closers[0] if closers else None
    budget = minutes - (closer.minutes if closer else 0.0)

    picked: list[Drill] = []
    # Soft tissue is capped at 2 picks so a hotspot-heavy profile can't
    # spend the whole slot rolling — every session keeps room for the
    # stretch/capsule tier (observed in the S1 smoke: 4 soft-tissue
    # picks left zero stretch minutes).
    # Control work sits BETWEEN rolling and stretching: the pattern gets
    # rehearsed while the tissue is warm and before the athlete goes
    # passive. Capped by the profile so a generic athlete keeps most
    # of the slot for stretching while an assessment-driven one leans
    # on the control tier.
    for cap, tier in ((2, _tier("soft_tissue", want)),
                      (control_cap, _tier("control", want, strict=True)),
                      (99, _tier("stretch", want)),
                      (99, _tier("stretch", None))):
        if cap <= 0:
            continue
        taken = 0
        for d in tier:
            if d in picked:
                continue
            if budget - d.minutes < -0.5:      # ~30s grace, no more
                continue
            picked.append(d)
            budget -= d.minutes
            taken += 1
            if budget < 2.0 or taken >= cap:
                break
        if budget < 2.0:
            break

    if closer:
        picked.append(closer)
    return picked


def side_bias_notes(drills: list[Drill], profile: dict) -> dict[str, str]:
    """profile['bias'] = {"side": "right", "areas": [...], "factor": 2}
    → a per-drill dosing note for every non-closer drill that serves a
    biased area ("right side ~2x the time"). Empty when no bias."""
    bias = profile.get("bias") or {}
    side = str(bias.get("side") or "").strip()
    areas = set(bias.get("areas") or [])
    if not side or not areas:
        return {}
    try:
        factor = float(bias.get("factor") or 2)
    except (TypeError, ValueError):
        factor = 2.0
    note = f"{side} side ~{factor:g}x the time"
    return {d.key: note for d in drills
            if d.sided and d.kind != "downreg" and areas & set(d.areas)}


def render(drills: list[Drill], minutes: float, header: str,
           whys: dict[str, str] | None = None,
           preface: str | None = None,
           notes: dict[str, str] | None = None) -> str:
    """Telegram-friendly deterministic render. Never empty if `drills`
    is non-empty; coach.py guarantees non-empty via compose(). `whys`
    (drill key → reason) puts a one-line rationale under each drill —
    the owner's 2026-09-03 ask: explain the choice, tied to today's
    workout. `preface` is the one-line context (what today loaded).
    `notes` (drill key → dosing note, e.g. the side bias) rides on the
    cue line."""
    total = sum(d.minutes for d in drills)
    lines = [header,
             f"_~{int(round(total))} min including transitions "
             f"(asked: {int(round(minutes))})_"]
    if preface:
        lines.append(preface)
    lines.append("")
    whys = whys or {}
    notes = notes or {}
    for d in drills:
        lines.append(f"*{_fmt_min(d.minutes)} — {d.name}*")
        if whys.get(d.key):
            lines.append(f"  why: {whys[d.key]}")
        cue = d.cue
        if notes.get(d.key):
            cue = f"{cue} ({notes[d.key]})"
        lines.append(f"  _{cue}_")
    lines.append("")
    lines.append("Not the flavor you want? Tell me the area or the vibe "
                 "and I'll re-cut it.")
    return "\n".join(lines)


def _fmt_min(m: float) -> str:
    return f"{m:g} min"
