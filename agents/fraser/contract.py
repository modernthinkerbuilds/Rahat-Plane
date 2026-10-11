"""agents.fraser.contract — the workout-design contract (2026-10-11).

Bourdain's asks go through ADR-018's loop (understand → contract → execute →
verify → judge → control → render → record). A workout design did not: Fraser
generated once, Miya's synthesizer re-voiced it, and nothing checked the
result. On 2026-10-11 the owner needed nine messages and twenty minutes to
get a session, and still got one with no loads, a five-minute cool-down, a
60-minute EMOM, wall balls in an office gym, deadlifts the day before the
gym's deadlift day and three different calorie gaps.

This module is the contract and its mechanical verifier:

  * `Constraints` — what the design must honour: the minutes, the calorie
    target, movements to avoid (the request's "no X" / "can't do X", "no X
    in the WOD", the profile's missing equipment, the gym's coming
    sessions), movements the athlete asked for by name, the formats asked
    for (AMRAP, EMOM), the style, and Kobe's week numbers.
  * `read_request(text)` — what one message says about the design.
  * `verify(text, c)` — the findings, each an id and one line:
      - sections    warm-up, strength, metcon and cool-down are all there,
                    and every format the athlete asked for
      - warm_up     the warm-up is 8+ minutes
      - cool_down   the cool-down is 8+ minutes and has the house style's
                    trap release and a breathing drill (coach.py, Part 6)
      - loads       every loaded movement carries its weight; a strength
                    lift in kg or lb, not only a %; never "pick a
                    moderate weight"
      - cues        every movement in the strength block and the metcon
                    has its how-to: a cue in italics, a "Cue:" or
                    "Standards:" line, or the cue in plain words
      - duration    no single EMOM / AMRAP block longer than the session
                    leaves room for; the blocks add up to the asked length
                    (±10 min)
      - excluded    nothing the athlete can't do, or said no to
      - overlap     nothing the gym programs in the coming days (two by
                    default; the week when the athlete asks to look at it)
      - distinct    strength and the metcon don't share a movement
      - calories    an "Estimated burn: A–B kcal" line, credible for the
                    length, reaching what this session should burn; no
                    sentence ties the week to a number /week doesn't say
  * `week_line(c, estimate)` — the week's numbers under the session: Kobe's
    (the /week numbers), with the arithmetic done here, not by the model.
  * `movements_in(text)` — the canonical movements a text names.
  * `repair_note(findings)` — what to tell the model to fix.

Deterministic, no model, no I/O. The composer runs it after every design,
repairs once, and says what is still unverified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── movements ──────────────────────────────────────────────────────────
# id → (words that name it). Matched as whole words, case-insensitive; a
# plural "s" or "es" is allowed ("snatches", "push presses"). Order matters where one name contains another:
# longer phrases are tried first by the matcher.
MOVEMENTS: dict[str, tuple[str, ...]] = {
    "deadlift": ("deadlift", "dead lift", "rdl", "romanian deadlift", "sumo deadlift"),
    "back_squat": ("back squat",),
    "front_squat": ("front squat",),
    "goblet_squat": ("goblet squat",),
    "overhead_squat": ("overhead squat",),
    "squat": ("squat", "air squat"),
    "clean": ("clean", "power clean", "squat clean", "hang clean"),
    "snatch": ("snatch", "power snatch", "squat snatch"),
    "db_snatch": ("dumbbell snatch", "db snatch", "alternating dumbbell snatch", "kettlebell snatch"),
    "thruster": ("thruster",),
    "wall_ball": ("wall ball", "wallball", "wall-ball"),
    "bench_press": ("bench press", "floor press", "db bench", "dumbbell bench", "incline press", "chest press"),
    "strict_press": ("strict press", "shoulder press", "overhead press", "z-press", "z press", "seated press"),
    "push_press": ("push press", "push jerk", "jerk"),
    "push_up": ("push-up", "push up", "pushup", "pike push-up", "pike push up"),
    "pull_up": ("pull-up", "pull up", "pullup", "chest-to-bar", "chest to bar", "c2b", "ring row"),
    "chin_up": ("chin-up", "chin up", "chinup"),
    "muscle_up": ("muscle-up", "muscle up"),
    "toes_to_bar": ("toes-to-bar", "toes to bar", "t2b", "knees-to-elbow", "knees to elbow"),
    "hanging_knee_raise": ("hanging knee raise", "hanging leg raise", "hanging knee raises", "hanging leg raises"),
    "handstand": ("handstand", "hspu", "wall walk"),
    "kb_swing": ("kettlebell swing", "kb swing", "russian swing", "american swing", "kettlebell swings"),
    "burpee": ("burpee", "devil press", "devil's press", "devils press"),
    "box_jump": ("box jump", "box jump over", "box jump-over"),
    "step_up": ("step-up", "step up", "step-over", "step over", "box step"),
    "lunge": ("lunge", "walking lunge", "reverse lunge", "cossack squat"),
    "double_under": ("double under", "double-under", "jump rope", "single under", "skipping"),
    "row_erg": ("row", "rowing", "rower", "calorie row", "cal row", "meter row", "m row"),
    "db_row": ("dumbbell row", "db row", "bent-over row", "bent over row", "barbell row", "chest-supported row",
               "chest supported row", "chest-supported", "chest supported", "renegade row", "single-arm row",
               "single arm row"),
    "bike": ("air bike", "assault bike", "echo bike", "bikeerg", "bike erg", "bike", "calorie bike", "cal bike"),
    "run": ("run", "running", "jog", "treadmill"),
    "ski": ("ski erg", "skierg", "ski"),
    "farmers_carry": ("farmers carry", "farmer's carry", "farmer carry", "farmers' carry", "suitcase carry",
                      "front rack carry", "overhead carry"),
    "sit_up": ("sit-up", "sit up", "situp", "abmat", "v-up", "v up", "v-ups", "hollow rock", "hollow hold",
               "toes to bar sit-up"),
    "plank": ("plank",),
    "ghd": ("ghd",),
    "pull_apart": ("pull-apart", "pull apart", "pass-through", "pass through", "face pull"),
}
MONOSTRUCTURAL = {"row_erg", "bike", "run", "ski"}
# Movements that need a bar to hang from.
HANGING = {"pull_up", "chin_up", "muscle_up", "toes_to_bar", "hanging_knee_raise"}
# Movements that are always done with a weight. Others (lunges, step-ups,
# a devil press) are loaded when their line names the implement.
LOADED = {"deadlift", "back_squat", "front_squat", "goblet_squat", "overhead_squat", "clean", "snatch", "db_snatch",
          "thruster", "wall_ball", "bench_press", "strict_press", "push_press", "kb_swing", "farmers_carry", "db_row"}
# Which movements the profile's missing equipment rules out.
EQUIPMENT_MOVEMENTS: dict[str, set[str]] = {
    "wall_balls": {"wall_ball"}, "med_ball": {"wall_ball"},
    "pull_up_rig": set(HANGING), "pull_up_bar": set(HANGING),
    "jump_rope": {"double_under"},
}
# The profile's blacklist, in its own words.
BLACKLIST_MOVEMENTS: dict[str, set[str]] = {
    "muscle_up": {"muscle_up"}, "handstand": {"handstand"}, "overhead_squat": {"overhead_squat"},
    "snatch_in_strength": set(),      # technique-only: the composer tells the model; nothing to ban outright
}
# How a reply names a movement, for a finding.
LABEL = {m: words[0] for m, words in MOVEMENTS.items()}
LABEL.update({"row_erg": "rowing", "db_row": "dumbbell row", "bike": "bike", "kb_swing": "kettlebell swing",
              "sit_up": "sit-ups", "db_snatch": "dumbbell snatch", "pull_apart": "band pull-aparts"})

_ALL_WORDS = sorted(((w, m) for m, ws in MOVEMENTS.items() for w in ws), key=lambda x: -len(x[0]))
_WORD_RE = re.compile("|".join(r"(?<![a-z-])(" + re.escape(w).replace(r"\ ", r"[\s-]+") + r")(?:es|s)?(?![a-z-])"
                               for w, _ in _ALL_WORDS), re.I)
_WORD_TO_ID = {w.lower(): m for w, m in _ALL_WORDS}


def movements_in(text: str) -> set[str]:
    """The canonical movements `text` names."""
    out: set[str] = set()
    for m in _WORD_RE.finditer(text or ""):
        word = re.sub(r"[\s-]+", " ", m.group(0).lower())
        hit = (_WORD_TO_ID.get(word) or _WORD_TO_ID.get(word[:-1]) or _WORD_TO_ID.get(word[:-2])     # "snatches"
               or _WORD_TO_ID.get(word.replace(" ", "-")))
        if hit:
            out.add(hit)
    # "row" inside "dumbbell row" is the dumbbell row, not the erg.
    if "db_row" in out and "row_erg" in out and not re.search(
            r"\b(?:cal(?:orie)?s?\s+row|\d+\s*m\s+row|row(?:er|ing)\b|\brow\s+for|on the rower|row\s+\d)", text, re.I):
        out.discard("row_erg")
    return out


def _names(moves: set[str]) -> str:
    return ", ".join(sorted(LABEL.get(m, m) for m in moves))


# ── the request ────────────────────────────────────────────────────────
# The owner dictates: his phone wrote "double understood" for double unders
# and "lunches" for lunges (live, 2026-10-11). Requests only — a design is
# typed by the model.
_DICTATION = (
    (re.compile(r"\bdouble[\s-]+under[\s-]?stood\b|\bdouble[\s-]+understand\b", re.I), "double unders"),
    (re.compile(r"\blunches\b", re.I), "lunges"),
    (re.compile(r"\bamra\b", re.I), "amrap"),
)
_NO_LIST_RE = re.compile(
    r"\b(?:no|without|skip|avoid|not?\s+(?:any|more))\s+([a-z][a-z\s,/&'-]{2,80}?)"
    r"(?=\s+(?:in|for|since|because|today|please|this|that|though|but|and\s+(?:i|we|make|find|give)|as\b)"
    r"|,\s*(?:i|we|you|it|this|that|but)\b|[.;!?\n]|$)",
    re.I)
# "No push up, air bike, burpees in the WOD": the metcon only, not the warm-up.
_SCOPED_RE = re.compile(r"\s+(?:in|for)\s+(?:the\s+|my\s+|your\s+|this\s+)?(?:wod|metcon|amrap|emom|conditioning)\b",
                        re.I)
_CANT_RE = re.compile(r"\b(?:i\s+)?(?:can'?t|cannot|can\s+not|unable to|don'?t have|no access to)\s+(?:do\s+|use\s+)?"
                      r"([a-z][a-z\s,/&'-]{2,80}?)(?=\s+(?:since|because|today|here|at\b|in\b|so\b|or\b)"
                      r"|,\s*(?:i|we|you|it|this|that|but)\b|[.;!?\n]|$)", re.I)
_CAN_RE = re.compile(r"\b(?:i\s+)?can\s+do\s+([a-z][a-z\s,/&'-]{2,60}?)(?=\s+(?:for|in|today|though|but)|[.;!?\n]|$)"
                     r"|\b([a-z][a-z\s'-]{2,40}?)\s+(?:is|are)\s+fine\b", re.I)
# A movement the athlete asks for by name ("use the bench press", "maybe
# dumbbell snatch will work") is allowed whatever the profile or the gym's
# week says; "tomorrow is deadlifts" names one without asking for it.
_WANT_RE = re.compile(r"\b(?:use|add|include|want|try|prefer|keep|give me|let'?s do)\s+(?:the\s+|a\s+|some\s+|heavy\s+)?"
                      r"([a-z][a-z\s,/&'-]{2,60}?)(?=\s+(?:for|in|today|instead|or\s+something)|[.;!?\n]|$)"
                      r"|\bmaybe\s+(?:even\s+)?([a-z][a-z\s'-]{2,40}?)"
                      r"(?=\s+(?:will|would|could|should|might|may)\s+work|\s+(?:for|right|and|or|then)\b|[,.;!?\n]|$)",
                      re.I)
_MIN_RE = re.compile(r"(\d{2,3})\s*(?:-\s*)?(?:min(?:ute)?s?|')(?![a-z])", re.I)
_BLOCK_AFTER_RE = re.compile(r"\s*(?:long\s+)?(?:emom|amrap|engine|block|piece|intervals?|time\s*cap|cap)\b", re.I)
_KCAL_RE = re.compile(r"(?<![\d,.])(\d{1,2},\d{3}|\d{3,4})\s*(?:k?cal(?:orie)?s?)\b", re.I)
_STYLE_RE = re.compile(r"\b(prvn|house style|gemini style|standard style|class style)\b", re.I)
_OFFICE_RE = re.compile(r"\b(office gym|hotel gym|at home|home gym|garage gym|travel(?:ling)?|on the road)\b", re.I)
# "Look at my workouts for next week": read the gym's whole coming week.
_WEEK_LOOK_RE = re.compile(
    r"\b(?:workouts?|wods?|programming|sessions?|classes|schedule)\s+(?:for\s+|of\s+)?(?:the\s+)?"
    r"(?:next|coming|upcoming|rest\s+of\s+the)\s+week\b"
    r"|\b(?:next|coming|upcoming)\s+week'?s\s+(?:workouts?|wods?|programming|sessions?|classes)\b"
    r"|\bthe\s+(?:rest\s+of\s+the|whole)\s+week'?s?\s+(?:workouts?|wods?|programming|sessions?|classes)\b", re.I)
# "Close the calorie gap for the week": size the session to the week.
_CLOSE_WEEK_RE = re.compile(
    r"\b(?:close|closing|fill|cover|make\s+up|catch\s+up(?:\s+on)?)\b[^.?!\n]{0,30}?"
    r"\b(?:gap|deficit|week|weekly|target|calories|calorie|burn)\b"
    r"|\bcatch\s+up\b|\bbehind\s+(?:on|for)\s+(?:the|my)\s+week\b", re.I)
# An EMOM/AMRAP length the athlete asks for ("a 40 min EMOM").
_LONG_BLOCK_RE = re.compile(r"(\d{2,3})\s*[-–]?\s*min(?:ute)?s?\s*(?:long\s+)?(?:emom|amrap|engine)\b"
                            r"|\b(?:emom|amrap)\s*(?:of\s+|for\s+|x\s*)?(\d{2,3})\b", re.I)
# Formats asked for: "break the metcon into an AMRAP and EMOM".
_FORMAT_WORDS = {"amrap": r"\bamrap\b", "emom": r"\bemom\b|every minute on the minute",
                 "for_time": r"\bfor\s+time\b|\brft\b"}
FORMAT_LABEL = {"amrap": "AMRAP", "emom": "EMOM", "for_time": "For Time"}
_FORMAT_ASK_RE = re.compile(r"\b(?:into|give me|split|as|want|make it|switch to|change it to)\b([^.?!\n]{0,60})", re.I)


@dataclass
class Constraints:
    minutes: int | None = None
    kcal: int | None = None                 # the athlete's stated target for this session
    avoid: set[str] = field(default_factory=set)
    allow: set[str] = field(default_factory=set)
    metcon_avoid: set[str] = field(default_factory=set)    # "no burpees in the WOD"
    formats: set[str] = field(default_factory=set)         # "amrap", "emom", "for_time"
    style: str | None = None                # "prvn" | "house"
    place: str | None = None                # "office gym" …
    gym_days: int | None = None             # how far ahead the gym's week is read (None: 2 days)
    close_week: bool = False                # "close the calorie gap for the week"
    long_block: int | None = None           # an EMOM/AMRAP length the athlete asked for
    next_gym: dict[str, set[str]] = field(default_factory=dict)   # "MON 12 (tomorrow)" → movements
    week_remaining: int | None = None       # Kobe: kcal still needed this week (the /week number)
    week_days_left: int | None = None
    today_target: int | None = None
    session_cap: int | None = None          # Kobe: the tier's per-session ceiling
    cooldown_needs: tuple[str, ...] = ("neck", "breath")   # the house style's Part 6 (coach.py)

    def excluded(self) -> set[str]:
        """Never anywhere: what the athlete ruled out and the profile lacks."""
        return self.avoid - self.allow

    def programmed(self) -> set[str]:
        """Not in the work: what the gym programs in the coming days, less
        what the athlete asked for by name. Runs, rows and rides are fine."""
        return {m for ms in self.next_gym.values() for m in ms if m not in MONOSTRUCTURAL} - self.allow - self.avoid

    def merged_with(self, other: "Constraints") -> "Constraints":
        """This request on top of the earlier ones (a refinement keeps the
        earlier "no wall balls"). An allow lifts an earlier avoid; a new
        list of formats replaces the old one."""
        out = Constraints(**{**self.__dict__})
        out.avoid = (self.avoid | other.avoid) - other.allow
        out.metcon_avoid = (self.metcon_avoid | other.metcon_avoid) - other.allow
        out.allow = (self.allow | other.allow) - other.avoid - other.metcon_avoid
        out.formats = set(other.formats or self.formats)
        out.minutes = other.minutes or self.minutes
        out.kcal = other.kcal or self.kcal
        out.style = other.style or self.style
        out.place = other.place or self.place
        out.gym_days = other.gym_days or self.gym_days
        out.close_week = other.close_week or self.close_week
        out.long_block = other.long_block or self.long_block
        return out

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("avoid", "allow", "metcon_avoid", "formats"):
            d[k] = sorted(getattr(self, k))
        d["next_gym"] = {k: sorted(v) for k, v in self.next_gym.items()}
        d["cooldown_needs"] = list(self.cooldown_needs)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Constraints":
        d = dict(d or {})
        for k in ("avoid", "allow", "metcon_avoid", "formats"):
            d[k] = set(d.get(k) or [])
        d["next_gym"] = {k: set(v) for k, v in (d.get("next_gym") or {}).items()}
        d["cooldown_needs"] = tuple(d.get("cooldown_needs") or ("neck", "breath"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def read_request(text: str) -> Constraints:
    """What one message says about the design: its length, its calorie
    target, the movements it rules out or back in, the formats it asks for,
    and whether to read the gym's week or close the week's calories."""
    c = Constraints()
    low = (text or "").replace("’", "'").replace("‘", "'")     # phones type curly apostrophes
    for rx, word in _DICTATION:
        low = rx.sub(word, low)
    for m in _MIN_RE.finditer(low):
        if _BLOCK_AFTER_RE.match(low, m.end()):
            continue                       # "a 40 min EMOM" is a block, not the session
        c.minutes = int(m.group(1))
        break
    m = _KCAL_RE.search(low)
    if m:
        c.kcal = int(m.group(1).replace(",", ""))
    m = _STYLE_RE.search(low)
    if m:
        c.style = "prvn" if "prvn" in m.group(1).lower() else "house"
    m = _OFFICE_RE.search(low)
    if m:
        c.place = m.group(1).lower()
    for rx in (_NO_LIST_RE, _CANT_RE):
        for m in rx.finditer(low):
            moves = movements_in(m.group(1))
            if rx is _NO_LIST_RE and _SCOPED_RE.match(low, m.end(1)):
                c.metcon_avoid |= moves
            else:
                c.avoid |= moves
    for rx in (_CAN_RE, _WANT_RE):
        for m in rx.finditer(low):
            c.allow |= movements_in(m.group(1) or m.group(2) or "")
    if re.search(r"\bcan'?t\s+do\s+(?:any\s+)?hang", low, re.I):
        c.avoid |= HANGING
    if _WEEK_LOOK_RE.search(low):
        c.gym_days = 7
    if _CLOSE_WEEK_RE.search(low):
        c.close_week = True
    for m in _LONG_BLOCK_RE.finditer(low):
        c.long_block = max(c.long_block or 0, int(m.group(1) or m.group(2)))
    for m in _FORMAT_ASK_RE.finditer(low):
        for f, words in _FORMAT_WORDS.items():
            if re.search(words, m.group(1), re.I):
                c.formats.add(f)
    c.avoid -= c.allow
    c.metcon_avoid -= c.allow
    return c


def profile_exclusions(profile) -> set[str]:
    """Movements the athlete's profile rules out: missing equipment and the
    blacklist. `profile` is core.athlete_profile.AthleteProfile-like."""
    out: set[str] = set()
    for item in getattr(profile, "equipment_unavailable", None) or []:
        out |= EQUIPMENT_MOVEMENTS.get(str(item).lower(), set())
    for item in getattr(profile, "movement_blacklist", None) or []:
        out |= BLACKLIST_MOVEMENTS.get(str(item).lower(), set())
    return out


# ── the design text ────────────────────────────────────────────────────
_HEADER_RE = re.compile(r"^\s*(?:#{1,4}\s*|\*\*)?\s*(?:part\s*\d+\s*[:.\-–—]\s*)?([^\n*#]{2,80}?)\s*(?:\*\*)?\s*$", re.I | re.M)
_KIND_WORDS = (
    ("warmup", r"warm[\s-]*up|primer|cash[\s-]*in|prep\b|activation"),
    ("cooldown", r"cool[\s-]*down|recovery|down[\s-]*regulation|flush"),
    ("strength", r"strength|lift|press program|squat program|chest program"),
    ("metcon", r"metcon|wod|conditioning|engine|amrap|emom|for time|rft|intervals|the workout|workout\b|main work"),
    ("accessory", r"accessor|core\b|finisher|extra credit"),
)
_MINUTES_IN_RE = re.compile(r"(\d{1,3})\s*(?:-|–|to)?\s*(\d{1,3})?\s*(?:min(?:ute)?s?\b|')", re.I)
_BLOCK_MIN_RE = re.compile(
    r"(?:(\d{1,3})\s*[-–]?\s*min(?:ute)?s?\s*(?:emom|amrap)|(?:emom|amrap|every minute on the minute)[^.\n]{0,40}?"
    r"(?:for|x)\s*(\d{1,3})\s*min(?:ute)?s?"
    r"|\b(?:emom|amrap)\s*(?:x\s*)?(\d{2,3})\b(?!\s*(?:sec|secs|seconds|s\b|cal|kcal|m\b|meters?|reps?|rounds?)))", re.I)
_KG_LB_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:/\s*\d+(?:[.,]\d+)?\s*)?(?:kg|kgs|kilos?|lb|lbs|pounds?|#)(?![a-z])", re.I)
_PCT_RE = re.compile(r"\d{1,3}\s*%")
_VAGUE_LOAD_RE = re.compile(
    r"\b(?:pick|choose|select|use|grab|build to|work up to)\s+(?:a\s+|an\s+)?(?:moderate|challenging|heavy|light|comfortable|"
    r"appropriate|manageable)\s+(?:weight|load|dumbbells?|kettlebell|bell)\b"
    r"|\b(?:moderate|challenging|heavy|light|comfortable)\s+(?:weight|load|dumbbells?|kettlebell)\b(?![^\n]{0,40}\d+\s*(?:kg|lb))",
    re.I)
_IMPLEMENT_RE = re.compile(r"\b(?:dumbbells?|db|dbs|kettlebells?|kb|kbs|barbell|bb|weighted|front[\s-]rack|sandbag|"
                           r"med(?:icine)?\s*ball)\b", re.I)
_BODYWEIGHT_RE = re.compile(r"\b(?:bodyweight|body\s*weight|bw|empty\s+bar|pvc|band(?:ed)?|unweighted|no\s+weight)\b", re.I)
_NECK_RE = re.compile(r"\b(trap|neck|levator|scapula|upper back release|lacrosse)\b", re.I)
_BREATH_RE = re.compile(r"\b(breath|exhale|4-8|4/8|box breathing|nasal)\w*", re.I)
# A movement's how-to: italics, a cue label, the cue words, or an
# instruction in brackets.
_ITALIC_RE = re.compile(r"(?<![*\w])\*(?![*\s])[^*\n]{3,}?(?<![*\s])\*(?![*\w])"
                        r"|(?<![\w_])_(?![_\s])[^_\n]{3,}?(?<![_\s])_(?![\w_])")
_CUE_LABEL_RE = re.compile(r"\b(?:cues?|coach'?s\s+(?:note|cue)|form|set[\s-]?up|technique|focus|key|tips?|how|"
                           r"standards?|breathing|execution)\s*:\s*\S", re.I)
_CUE_WORDS_RE = re.compile(
    r"\b(?:shoulders?\s+(?:back|down|packed)|chest\s+up|long\s+neck|eyes\s+(?:on|down|forward|ahead)|gaze|exhale|"
    r"inhale|breathe|brace[ds]?|bracing|heels?|soft\s+landing|neutral\s+(?:spine|neck|back)|flat\s+back|"
    r"lock\s*-?\s*out|drive\s+(?:through|from|with|the|your)|hinge|squeeze|focus\s+on|keep\s+(?:the|your|a|it)|"
    r"elbows?\s+(?:in|tucked|high|at|45)|knees?\s+(?:out|track|over)|hips?\s+(?:back|through|drive|hinge)|"
    r"core\s+(?:tight|braced|engaged)|tight\s+core|control(?:led)?\s+(?:the\s+)?(?:descent|eccentric|lowering|negative)|"
    r"pause\s+(?:at|in)|punch|pull\s+(?:to|toward|from)|stand\s+tall|full\s+(?:extension|range|lockout))\b", re.I)
_CUE_BRACKET_RE = re.compile(
    r"\((?=[^)]{12,}\))[^)]*\b(?:keep|maintain|use|focus|drive|stay|avoid|control|step|pull|push|press|land|squeeze|"
    r"brace|breathe|exhale|inhale|engage|protect|hinge|lock|stand|tuck|pause|sink|reach|lower)\b[^)]*\)", re.I)


def _has_cue(text: str) -> bool:
    return bool(_ITALIC_RE.search(text) or _CUE_LABEL_RE.search(text) or _CUE_WORDS_RE.search(text)
                or _CUE_BRACKET_RE.search(text))


@dataclass
class Section:
    kind: str
    title: str
    body: str

    @property
    def minutes(self) -> int | None:
        """The block's length: from its title ("Warm-up (12 min)"), else the
        sum of the minutes its body names ("3 minutes easy … then 10
        minutes of …")."""
        m = _MINUTES_IN_RE.search(self.title)
        if m:
            return max(int(m.group(1)), int(m.group(2) or m.group(1)))
        total = 0
        for m in _MINUTES_IN_RE.finditer(self.body):
            total += int(m.group(2) or m.group(1))
        return total or None


def sections(text: str) -> list[Section]:
    """The design split at its headers, each classed by its words."""
    out: list[Section] = []
    lines = (text or "").splitlines()
    cur: Section | None = None
    for line in lines:
        stripped = line.strip()
        title = re.sub(r"[#*]+", "", stripped).strip()
        kind = "other"
        for k, words in _KIND_WORDS:
            if re.search(words, title, re.I):
                kind = k
                break
        # A markdown header always opens a section. A bold line opens one
        # only when it names a section (a bold "A. Strict Press" is body).
        is_header = bool(stripped) and len(stripped) < 90 and (
            stripped.startswith("#")
            or (stripped.startswith("**") and stripped.endswith("**")
                and (kind != "other" or re.match(r"part\s*\d", title, re.I))))
        if is_header:
            cur = Section(kind, title, "")
            out.append(cur)
        elif cur is not None:
            cur.body += line + "\n"
    return out


def _windows(body: str) -> list[tuple[set[str], str, str]]:
    """(movements, window, first line) for each line naming a movement
    other than the erg or a run; the window runs to the next such line, a
    blank line, or four lines, so a lift's sub-bullets belong to it."""
    lines = body.splitlines()
    out: list[tuple[set[str], str, str]] = []
    i = 0
    while i < len(lines):
        moves = movements_in(lines[i]) - MONOSTRUCTURAL
        if not moves:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and j - i <= 4 and lines[j].strip() and not (movements_in(lines[j]) - MONOSTRUCTURAL):
            j += 1
        out.append((moves, "\n".join(lines[i:j]), lines[i]))
        i = j
    return out


def _unloaded(body: str, *, strength: bool) -> set[str]:
    """Loaded movements with no weight: a strength lift needs kg or lb in its
    lines; a metcon line may lean on a "Dumbbells: 2x22.5 kg" line."""
    general = any(_KG_LB_RE.search(ln) and not (movements_in(ln) - MONOSTRUCTURAL) for ln in body.splitlines())
    missing: set[str] = set()
    for moves, window, first in _windows(body):
        loaded = moves & LOADED
        if not loaded and _IMPLEMENT_RE.search(first):
            loaded = set(moves)
        if not loaded or _BODYWEIGHT_RE.search(first):
            continue
        if _KG_LB_RE.search(window):
            continue
        if not strength and (general or _PCT_RE.search(window)):
            continue
        missing |= loaded
    return missing


def _uncued(body: str) -> set[str]:
    """Movements with no how-to anywhere in the block. A "Standards" line
    further down counts for the movement it names."""
    seen: set[str] = set()
    cued: set[str] = set()
    for moves, window, _first in _windows(body):
        seen |= moves
        if _has_cue(window):
            cued |= moves
    return seen - cued


@dataclass
class Finding:
    id: str
    line: str

    def __str__(self) -> str:
        return f"{self.id}: {self.line}"


def _kinds(secs: list[Section]) -> dict[str, list[Section]]:
    by: dict[str, list[Section]] = {}
    for s in secs:
        by.setdefault(s.kind, []).append(s)
    return by


def block_cap(c: Constraints) -> int:
    """The longest single EMOM/AMRAP the session leaves room for: 30 min,
    more in a long session (a 50-minute engine fits 90 minutes), or what
    the athlete asked for by length."""
    cap = max(30, (c.minutes or 0) - 40)
    return max(cap, c.long_block or 0)


# ── the calories ───────────────────────────────────────────────────────
# Kobe owns the week; the model owns only its estimate of THIS session. The
# live failure (2026-10-11): one session said it would "close today's 523
# kcal gap" (Kobe's gap-vs-plan), the next "we're behind by about 1,300",
# while /week said 1,949 to go. The week's line is now written here.
REALISTIC_KCAL_PER_MIN = 11     # a hard session, warm-up to cool-down: the owner's own "800 in 75 min"
PLAUSIBLE_KCAL_PER_MIN = 14     # above this a claimed burn is not credible
DEFAULT_SESSION_CAP = 1100      # Kobe's per-session cap at the default tier
WEEK_LINE_MARK = "🔥 Week:"
UNVERIFIED_MARK = "⚠️ Not verified:"
_N = r"(\d{1,2},\d{3}|\d{2,4})"
_EST_LINE_RE = re.compile(r"estimated\s+(?:calorie\s+)?(?:burn|kcal|calories)\s*[:=]?\s*(?:≈|~|about\s+)?\s*"
                          + _N + r"(?:\s*(?:-|–|to)\s*" + _N + r")?\s*k?cal", re.I)
_EST_ANY_RE = re.compile(r"\b(?:estimat\w*|est\.)[^.\n]{0,40}?(?<![\d,.])" + _N
                         + r"(?:\s*(?:-|–|to)\s*" + _N + r")?\s*k?cal", re.I)
_KCAL_ANY_RE = re.compile(r"(?<![\d,.])(\d{1,2},\d{3}|\d{3,4})(?:\s*(?:-|–|to)\s*(\d{1,2},\d{3}|\d{3,4}))?"
                          r"\s*(?:k?cal(?:orie)?s?)\b", re.I)
_WEEK_WORDS_RE = re.compile(r"\b(?:gap|deficit|behind|short(?:fall)?|to\s+go|left|remaining|still\s+needs?|"
                            r"the\s+week|this\s+week|weekly|week's|catch\s+up)\b", re.I)
_ESTIMATE_BEFORE_RE = re.compile(r"estimat|this\s+session|session\s+burns?|\bburn(?:s|ing)?\b", re.I)


def _kcal(s: str) -> int:
    return int(s.replace(",", ""))


def realistic_max(c: Constraints) -> int:
    """What a session of this length can realistically burn."""
    cap = c.session_cap or DEFAULT_SESSION_CAP
    return min(cap, REALISTIC_KCAL_PER_MIN * c.minutes) if c.minutes else cap


def session_target(c: Constraints) -> int | None:
    """What THIS session should burn: the athlete's own number, else — when
    they asked to close the week — the week's remainder; never more than a
    realistic session."""
    top = realistic_max(c)
    if c.kcal:
        return min(c.kcal, top)
    if c.close_week and c.week_remaining:
        return min(c.week_remaining, top)
    return None


def session_estimate(text: str) -> tuple[int, int] | None:
    """THIS session's estimated burn as (low, high): the "Estimated burn:"
    line the contract asks for, else the last "estimated … kcal"."""
    for rx in (_EST_LINE_RE, _EST_ANY_RE):
        hits = [m for m in rx.finditer(strip_marks(text)) if _kcal(m.group(1)) >= 50]
        if hits:
            m = hits[-1]
            lo = _kcal(m.group(1))
            hi = _kcal(m.group(2)) if m.group(2) else lo
            return (min(lo, hi), max(lo, hi))
    return None


def week_line(c: Constraints, estimate: tuple[int, int] | None) -> str | None:
    """The week under the session, from Kobe's numbers: what is to go (the
    /week number), what this session burns, what is left after it."""
    if c.week_remaining is None:
        return None
    w = c.week_remaining
    days = c.week_days_left or 1
    when = "today, the last day of the week" if days == 1 else f"over {days} days, today included"
    line = f"{WEEK_LINE_MARK} {w:,} kcal to go {when} (the /week number)."
    if not estimate:
        return line
    lo, hi = estimate
    line += f" This session ≈ {lo:,}–{hi:,}." if lo != hi else f" This session ≈ {lo:,}."
    a_lo, a_hi = max(w - hi, 0), max(w - lo, 0)
    if a_hi == 0:
        return line + " That closes the week."
    after = f"{a_lo:,}–{a_hi:,}" if a_lo != a_hi else f"{a_lo:,}"
    if days == 1:
        return line + f" After it ≈ {after} still to go before the week resets at midnight."
    per = (a_lo + a_hi) // 2 // (days - 1)
    return line + f" After it ≈ {after} over the next {days - 1} day{'s' if days > 2 else ''} (≈{per:,}/day)."


def strip_marks(text: str) -> str:
    """The design without the lines this shell adds (the week line, the
    "Not verified" line), so a refinement never copies stale ones."""
    keep = [ln for ln in (text or "").splitlines()
            if not ln.lstrip().startswith((WEEK_LINE_MARK, UNVERIFIED_MARK))]
    return "\n".join(keep).strip()


def with_week_line(text: str, line: str | None) -> str:
    """The week line right under the "Estimated burn" line, else at the end."""
    if not line:
        return text
    lines = (text or "").rstrip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if _EST_LINE_RE.search(lines[i]):
            return "\n".join(lines[:i + 1] + [line] + lines[i + 1:])
    return (text or "").rstrip() + "\n\n" + line


def _week_claims(text: str, c: Constraints, est: tuple[int, int] | None) -> list[str]:
    """Sentences that tie the week to a number /week doesn't say."""
    if not c.week_remaining:
        return []
    w = c.week_remaining
    # (number, tolerance): the week's own number may be rounded; the
    # others must be the number itself ("523" is not today's 550).
    points = [(w, max(75, 0.05 * w))] + [(p, 10) for p in (c.today_target, c.kcal, session_target(c)) if p]
    ranges = [(max(w - est[1], 0), max(w - est[0], 0))] if est else []
    bad: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        if not _WEEK_WORDS_RE.search(sentence):
            continue
        for m in _KCAL_ANY_RE.finditer(sentence):
            if _ESTIMATE_BEFORE_RE.search(sentence[max(0, m.start() - 30):m.start()]):
                continue                   # "burning an estimated 800–900 kcal" is the session
            wrong = False
            for g in (m.group(1), m.group(2)):
                if not g:
                    continue
                n = _kcal(g)
                if not (any(abs(n - p) <= tol for p, tol in points) or any(lo - 75 <= n <= hi + 75 for lo, hi in ranges)):
                    wrong = True
            if wrong:
                bad.append(re.sub(r"[*_#]+", "", sentence).strip()[:90])
                break
    return list(dict.fromkeys(bad))


def _calorie_findings(text: str, c: Constraints) -> list[Finding]:
    out: list[Finding] = []
    est = session_estimate(text)
    if not est:
        out.append(Finding("calories", "no 'Estimated burn: A–B kcal' line for this session"))
    else:
        lo, hi = est
        credible = PLAUSIBLE_KCAL_PER_MIN * c.minutes if c.minutes else None
        if credible and hi > credible:
            out.append(Finding("calories", f"an estimated {hi:,} kcal is not credible for {c.minutes} min "
                                           f"(≈{credible:,} at most)"))
        elif c.session_cap and hi > c.session_cap:
            out.append(Finding("calories", f"an estimated {hi:,} kcal is over Kobe's per-session cap of "
                                           f"{c.session_cap:,}"))
        target = session_target(c)
        if target and hi < 0.9 * target:
            out.append(Finding("calories", f"the estimate ({lo:,}–{hi:,} kcal) is short of the ~{target:,} this "
                                           f"session should burn; extend or add engine work"))
    for s in _week_claims(text, c, est):
        out.append(Finding("calories", f"take the week's numbers out of \"{s}\" — /week says "
                                       f"{c.week_remaining:,} to go, and that line is added under the session"))
    return out


def verify(text: str, c: Constraints, *, strength_expected: bool = True) -> list[Finding]:
    """The contract's findings on a design. Empty means it holds."""
    out: list[Finding] = []
    text = strip_marks(text)
    secs = sections(text)
    by = _kinds(secs)

    # sections, and the formats asked for
    missing = [k for k in ("warmup", "metcon", "cooldown") if k not in by]
    if strength_expected and "strength" not in by:
        missing.insert(1, "strength")
    if missing:
        names = {"warmup": "a warm-up", "strength": "a strength block", "metcon": "a metcon", "cooldown": "a cool-down"}
        out.append(Finding("sections", "missing " + ", ".join(names[k] for k in missing)))
    if c.formats and by.get("metcon"):
        blob = " ".join(s.title + " " + s.body for s in by["metcon"])
        lacking = [FORMAT_LABEL[f] for f in sorted(c.formats) if not re.search(_FORMAT_WORDS[f], blob, re.I)]
        if lacking:
            out.append(Finding("sections", "missing the " + " and the ".join(lacking) + " you asked for"))

    # warm-up and cool-down length: a short session (30 min or less) gets 5+, else 8+.
    floor = 5 if c.minutes and c.minutes <= 30 else 8
    warm = [s.minutes for s in by.get("warmup", []) if s.minutes]
    if warm and sum(warm) < floor:
        out.append(Finding("warm_up", f"the warm-up is {sum(warm)} min; it needs {floor} or more"))
    cool = [s.minutes for s in by.get("cooldown", []) if s.minutes]
    if cool and sum(cool) < floor:
        out.append(Finding("cool_down", f"the cool-down is {sum(cool)} min; it needs {floor} or more"))

    # cool-down content: the house style's Part 6
    if by.get("cooldown"):
        body = " ".join(s.title + " " + s.body for s in by["cooldown"])
        needs = []
        if "neck" in c.cooldown_needs and not _NECK_RE.search(body):
            needs.append("the trap/neck release")
        if "breath" in c.cooldown_needs and not _BREATH_RE.search(body):
            needs.append("a breathing drill")
        if needs:
            out.append(Finding("cool_down", "the cool-down is missing " + " and ".join(needs)))

    # loads: every loaded movement carries its weight
    for s in by.get("strength", []):
        miss = _unloaded(s.body, strength=True)
        if miss:
            out.append(Finding("loads", f"no weight in kg/lb for {_names(miss)} under '{s.title}'"))
    for s in by.get("metcon", []) + by.get("accessory", []):
        miss = _unloaded(s.body, strength=False)
        if miss:
            out.append(Finding("loads", f"no weight for {_names(miss)} under '{s.title}'"))
    for s in by.get("strength", []) + by.get("metcon", []) + by.get("accessory", []):
        for m in _VAGUE_LOAD_RE.finditer(s.body):
            out.append(Finding("loads", f"'{m.group(0)}' instead of a number under '{s.title}'"))

    # cues: every movement in the strength block and the metcon has its how-to
    for s in by.get("strength", []) + by.get("metcon", []):
        miss = _uncued(s.body)
        if miss:
            out.append(Finding("cues", f"no how-to for {_names(miss)} under '{s.title}'"))

    # duration
    cap = block_cap(c)
    long_blocks = sorted({n for m in _BLOCK_MIN_RE.finditer(text)
                          for n in [int(m.group(1) or m.group(2) or m.group(3))] if n > cap})
    for n in long_blocks:
        out.append(Finding("duration", f"a {n}-minute EMOM/AMRAP block; cap any one block at {cap}"))
    if c.minutes:
        timed = [s for s in secs if s.kind in ("warmup", "strength", "metcon", "accessory", "cooldown")]
        if len(timed) >= 2 and all(s.minutes for s in timed):      # every block timed, else no verdict
            total = sum(s.minutes for s in timed)
            if abs(total - c.minutes) > 10:
                out.append(Finding("duration", f"the blocks add up to {total} min; the ask was {c.minutes}"))

    # movements the athlete ruled out (anywhere; "in the WOD" means the
    # metcon), and the gym's coming sessions (in the work, not the
    # warm-up): read from the blocks, not the prose around them ("fresh for
    # deadlifts tomorrow")
    every = [s for s in secs if s.kind != "other"]
    work = [s for s in secs if s.kind in ("strength", "metcon", "accessory")]
    found = movements_in("\n".join(s.body for s in every)) if every else movements_in(text)
    in_work = movements_in("\n".join(s.body for s in work)) if work else found
    in_metcon = movements_in("\n".join(s.body for s in by.get("metcon", [])))
    ruled_out = sorted((found & (c.avoid - c.allow)) | (in_metcon & (c.metcon_avoid - c.allow)))
    if ruled_out:
        out.append(Finding("excluded", "uses " + ", ".join(LABEL.get(m, m) for m in ruled_out)))
    programmed: dict[str, str] = {}
    for day, ms in c.next_gym.items():
        for m in ms:
            if m not in MONOSTRUCTURAL:
                programmed.setdefault(m, day)
    overlap = sorted(m for m in in_work if m in programmed and m not in c.avoid and m not in c.allow)
    if overlap:
        out.append(Finding("overlap", "uses " + ", ".join(f"{LABEL.get(m, m)} (programmed {programmed[m]})"
                                                           for m in overlap)))

    # strength and metcon distinct
    strength_moves = set().union(*(movements_in(s.body) for s in by.get("strength", []))) if by.get("strength") else set()
    metcon_moves = set().union(*(movements_in(s.body) for s in by.get("metcon", []))) if by.get("metcon") else set()
    shared = sorted((strength_moves & metcon_moves) - MONOSTRUCTURAL - {"pull_apart"})
    if shared:
        out.append(Finding("distinct", "in both the strength block and the metcon: " + ", ".join(LABEL.get(m, m) for m in shared)))

    # the calories
    out += _calorie_findings(text, c)
    seen: set[tuple[str, str]] = set()
    return [f for f in out if not ((f.id, f.line) in seen or seen.add((f.id, f.line)))]


def repair_note(findings: list[Finding]) -> str:
    """The instruction for one more pass."""
    lines = ["Your previous session did not meet the athlete's contract. Fix every item below and keep "
             "everything else as it was:"]
    lines += [f"- {f.line}" for f in findings]
    return "\n".join(lines)


def unverified_line(findings: list[Finding]) -> str:
    """What the athlete is told when a repair still leaves findings."""
    kinds = {"sections": "a section is missing", "warm_up": "the warm-up is short", "cool_down": "the cool-down",
             "loads": "loads", "cues": "a movement's how-to", "duration": "the length",
             "excluded": "a movement you ruled out", "overlap": "the gym's coming sessions",
             "distinct": "a movement repeats", "calories": "the calorie estimate"}
    seen: list[str] = []
    for f in findings:
        k = kinds.get(f.id, f.id)
        if k not in seen:
            seen.append(k)
    return f"{UNVERIFIED_MARK} " + "; ".join(seen) + ". Check these before you start."


__all__ = ["MOVEMENTS", "MONOSTRUCTURAL", "HANGING", "LOADED", "LABEL", "FORMAT_LABEL", "Constraints", "Finding", "Section",
           "movements_in", "read_request", "profile_exclusions", "sections", "verify", "repair_note",
           "unverified_line", "block_cap", "realistic_max", "session_target", "session_estimate", "week_line",
           "with_week_line", "strip_marks", "WEEK_LINE_MARK", "UNVERIFIED_MARK"]
