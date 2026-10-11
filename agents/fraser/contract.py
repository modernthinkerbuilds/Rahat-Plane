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
    target, movements to avoid (the request's "no X" / "can't do X", the
    profile's missing equipment, tomorrow's gym programming), movements the
    athlete allowed, the style, and Kobe's week numbers.
  * `read_request(text)` — the avoid / allow words in a message.
  * `verify(text, c)` — the findings, each an id and one line:
      - sections    warm-up, strength, metcon and cool-down are all there
      - warm_up     the warm-up is 8+ minutes
      - cool_down   the cool-down is 8+ minutes and has the neck/trap
                    release and a breathing drill (the athlete's rule)
      - loads       the strength work carries loads in kg or lb, never
                    "pick a challenging weight"
      - duration    no single EMOM / AMRAP block over 30 min; the blocks
                    add up to the asked length (±10 min)
      - excluded    nothing the athlete can't do, or said no to
      - overlap     nothing the gym programs tomorrow
      - distinct    strength and the metcon don't share a movement
      - calories    the week's remaining kcal (Kobe's number) and the
                    session's estimate are stated
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
# plural "s" is allowed. Order matters where one name contains another:
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
_WORD_RE = re.compile("|".join(r"(?<![a-z-])(" + re.escape(w).replace(r"\ ", r"[\s-]+") + r")s?(?![a-z-])"
                               for w, _ in _ALL_WORDS), re.I)
_WORD_TO_ID = {w.lower(): m for w, m in _ALL_WORDS}


def movements_in(text: str) -> set[str]:
    """The canonical movements `text` names."""
    out: set[str] = set()
    for m in _WORD_RE.finditer(text or ""):
        word = re.sub(r"[\s-]+", " ", m.group(0).lower())
        hit = _WORD_TO_ID.get(word) or _WORD_TO_ID.get(word[:-1]) or _WORD_TO_ID.get(word.replace(" ", "-"))
        if hit:
            out.add(hit)
    # "row" inside "dumbbell row" is the dumbbell row, not the erg.
    if "db_row" in out and "row_erg" in out and not re.search(
            r"\b(?:cal(?:orie)?s?\s+row|\d+\s*m\s+row|row(?:er|ing)\b|\brow\s+for|on the rower|row\s+\d)", text, re.I):
        out.discard("row_erg")
    return out


# ── the request ────────────────────────────────────────────────────────
_NO_LIST_RE = re.compile(
    r"\b(?:no|without|skip|avoid|not?\s+(?:any|more))\s+([a-z][a-z\s,/&'-]{2,80}?)"
    r"(?=\s+(?:in|for|since|because|today|please|this|that|though|but|and\s+(?:i|we|make|find|give)|as\b)|[.;!?\n]|$)",
    re.I)
_CANT_RE = re.compile(r"\b(?:i\s+)?(?:can'?t|cannot|can\s+not|unable to|don'?t have|no access to)\s+(?:do\s+|use\s+)?"
                      r"([a-z][a-z\s,/&'-]{2,80}?)(?=\s+(?:since|because|today|here|at\b|in\b|so\b|or\b)|[.;!?\n]|$)", re.I)
_CAN_RE = re.compile(r"\b(?:i\s+)?can\s+do\s+([a-z][a-z\s,/&'-]{2,60}?)(?=\s+(?:for|in|today|though|but)|[.;!?\n]|$)"
                     r"|\b([a-z][a-z\s'-]{2,40}?)\s+(?:is|are)\s+fine\b", re.I)
# A movement the athlete asks for by name ("use the bench press", "maybe
# dumbbell snatch will work") is allowed whatever the profile or the gym's
# week says; "tomorrow is deadlifts" names one without asking for it.
_WANT_RE = re.compile(r"\b(?:use|add|include|want|try|prefer|keep|give me|let'?s do)\s+(?:the\s+|a\s+|some\s+|heavy\s+)?"
                      r"([a-z][a-z\s,/&'-]{2,60}?)(?=\s+(?:for|in|today|instead|or\s+something)|[.;!?\n]|$)"
                      r"|\bmaybe\s+(?:even\s+)?([a-z][a-z\s'-]{2,40}?)(?=\s+(?:will|would|could|should)\s+work|\s+for|[.;!?\n]|$)",
                      re.I)
_MIN_RE = re.compile(r"(\d{2,3})\s*(?:-\s*)?(?:min(?:ute)?s?|')\b", re.I)
_KCAL_RE = re.compile(r"(\d{3,4})\s*(?:k?cal(?:orie)?s?)\b", re.I)
_STYLE_RE = re.compile(r"\b(prvn|house style|gemini style|standard style|class style)\b", re.I)
_OFFICE_RE = re.compile(r"\b(office gym|hotel gym|at home|home gym|garage gym|travel(?:ling)?|on the road)\b", re.I)


@dataclass
class Constraints:
    minutes: int | None = None
    kcal: int | None = None                 # the athlete's stated target for this session
    avoid: set[str] = field(default_factory=set)
    allow: set[str] = field(default_factory=set)
    style: str | None = None                # "prvn" | "house"
    place: str | None = None                # "office gym" …
    next_gym: dict[str, set[str]] = field(default_factory=dict)   # "Mon 12 (tomorrow)" → movements
    week_remaining: int | None = None       # Kobe: kcal still needed this week
    week_days_left: int | None = None
    today_target: int | None = None
    cooldown_needs: tuple[str, ...] = ("neck", "breath")   # the athlete's cool-down rule

    def excluded(self) -> set[str]:
        return (self.avoid | {m for ms in self.next_gym.values() for m in ms if m not in MONOSTRUCTURAL}) - self.allow

    def merged_with(self, other: "Constraints") -> "Constraints":
        """This request on top of the earlier ones (a refinement keeps the
        earlier "no wall balls"). An allow lifts an earlier avoid."""
        out = Constraints(**{**self.__dict__})
        out.avoid = (self.avoid | other.avoid) - other.allow
        out.allow = (self.allow | other.allow) - other.avoid
        out.minutes = other.minutes or self.minutes
        out.kcal = other.kcal or self.kcal
        out.style = other.style or self.style
        out.place = other.place or self.place
        return out

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["avoid"], d["allow"] = sorted(self.avoid), sorted(self.allow)
        d["next_gym"] = {k: sorted(v) for k, v in self.next_gym.items()}
        d["cooldown_needs"] = list(self.cooldown_needs)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Constraints":
        d = dict(d or {})
        d["avoid"], d["allow"] = set(d.get("avoid") or []), set(d.get("allow") or [])
        d["next_gym"] = {k: set(v) for k, v in (d.get("next_gym") or {}).items()}
        d["cooldown_needs"] = tuple(d.get("cooldown_needs") or ("neck", "breath"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def read_request(text: str) -> Constraints:
    """What one message says about the design: its length, its calorie
    target, and the movements it rules out or back in."""
    c = Constraints()
    low = (text or "").replace("’", "'").replace("‘", "'")     # phones type curly apostrophes
    m = _MIN_RE.search(low)
    if m:
        c.minutes = int(m.group(1))
    m = _KCAL_RE.search(low)
    if m:
        c.kcal = int(m.group(1))
    m = _STYLE_RE.search(low)
    if m:
        c.style = "prvn" if "prvn" in m.group(1).lower() else "house"
    m = _OFFICE_RE.search(low)
    if m:
        c.place = m.group(1).lower()
    for rx in (_NO_LIST_RE, _CANT_RE):
        for m in rx.finditer(low):
            c.avoid |= movements_in(m.group(1))
    for rx in (_CAN_RE, _WANT_RE):
        for m in rx.finditer(low):
            c.allow |= movements_in(m.group(1) or m.group(2) or "")
    if re.search(r"\bcan'?t\s+do\s+(?:any\s+)?hang", low, re.I):
        c.avoid |= HANGING
    c.avoid -= c.allow
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
    r"(?:for|x)\s*(\d{1,3})\s*min(?:ute)?s?)", re.I)
_LOAD_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:kg|kgs|kilos?|lb|lbs|pounds?|#)\b|\d{1,3}\s*%", re.I)
_VAGUE_LOAD_RE = re.compile(
    r"\b(?:pick|choose|select|use|grab|build to|work up to)\s+(?:a\s+|an\s+)?(?:moderate|challenging|heavy|light|comfortable|"
    r"appropriate|manageable)\s+(?:weight|load|dumbbells?|kettlebell|bell)\b"
    r"|\b(?:moderate|challenging|heavy|light|comfortable)\s+(?:weight|load|dumbbells?|kettlebell)\b(?![^\n]{0,40}\d+\s*(?:kg|lb))",
    re.I)
_KCAL_NUM_RE = re.compile(r"(\d{1,2}[,.]\d{3}|\d{3,4})\s*(?:[-–]\s*(?:\d{1,2}[,.]\d{3}|\d{3,4})\s*)?k?cal", re.I)
_NECK_RE = re.compile(r"\b(trap|neck|levator|scapula|upper back release|lacrosse)\b", re.I)
_BREATH_RE = re.compile(r"\b(breath|exhale|4-8|4/8|box breathing|nasal)\w*", re.I)


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


def verify(text: str, c: Constraints, *, strength_expected: bool = True) -> list[Finding]:
    """The contract's findings on a design. Empty means it holds."""
    out: list[Finding] = []
    secs = sections(text)
    by = _kinds(secs)
    low = (text or "").lower()

    # sections
    missing = [k for k in ("warmup", "metcon", "cooldown") if k not in by]
    if strength_expected and "strength" not in by:
        missing.insert(1, "strength")
    if missing:
        names = {"warmup": "a warm-up", "strength": "a strength block", "metcon": "a metcon", "cooldown": "a cool-down"}
        out.append(Finding("sections", "missing " + ", ".join(names[k] for k in missing)))

    # warm-up and cool-down length: a short session (30 min or less) gets 5+, else 8+.
    floor = 5 if c.minutes and c.minutes <= 30 else 8
    warm = [s.minutes for s in by.get("warmup", []) if s.minutes]
    if warm and sum(warm) < floor:
        out.append(Finding("warm_up", f"the warm-up is {sum(warm)} min; it needs {floor} or more"))
    cool = [s.minutes for s in by.get("cooldown", []) if s.minutes]
    if cool and sum(cool) < floor:
        out.append(Finding("cool_down", f"the cool-down is {sum(cool)} min; it needs {floor} or more"))

    # cool-down content: the athlete's rule
    if by.get("cooldown"):
        body = " ".join(s.title + " " + s.body for s in by["cooldown"])
        needs = []
        if "neck" in c.cooldown_needs and not _NECK_RE.search(body):
            needs.append("the neck/trap release")
        if "breath" in c.cooldown_needs and not _BREATH_RE.search(body):
            needs.append("a breathing drill")
        if needs:
            out.append(Finding("cool_down", "the cool-down is missing " + " and ".join(needs)))

    # loads
    for s in by.get("strength", []):
        body = s.body
        if not _LOAD_RE.search(body):
            out.append(Finding("loads", f"no load in kg or lb under '{s.title}'"))
        for m in _VAGUE_LOAD_RE.finditer(body):
            out.append(Finding("loads", f"'{m.group(0)}' instead of a number under '{s.title}'"))
    for s in by.get("metcon", []):
        for m in _VAGUE_LOAD_RE.finditer(s.body):
            out.append(Finding("loads", f"'{m.group(0)}' instead of a number under '{s.title}'"))

    # duration
    long_blocks = sorted({int(m.group(1) or m.group(2)) for m in _BLOCK_MIN_RE.finditer(text or "")
                          if int(m.group(1) or m.group(2)) > 30})
    for n in long_blocks:
        out.append(Finding("duration", f"a {n}-minute EMOM/AMRAP block; cap any one block at 30"))
    if c.minutes:
        timed = [s for s in secs if s.kind in ("warmup", "strength", "metcon", "accessory", "cooldown")]
        if len(timed) >= 2 and all(s.minutes for s in timed):      # every block timed, else no verdict
            total = sum(s.minutes for s in timed)
            if abs(total - c.minutes) > 10:
                out.append(Finding("duration", f"the blocks add up to {total} min; the ask was {c.minutes}"))

    # movements the athlete ruled out, and the gym's next sessions: read from
    # the blocks, not the prose around them ("fresh for deadlifts tomorrow")
    work = [s for s in secs if s.kind != "other"]
    found = movements_in("\n".join(s.body for s in work)) if work else movements_in(text)
    ruled_out = sorted(found & (c.avoid - c.allow))
    if ruled_out:
        out.append(Finding("excluded", "uses " + ", ".join(LABEL.get(m, m) for m in ruled_out)))
    programmed = {m: day for day, ms in c.next_gym.items() for m in ms if m not in MONOSTRUCTURAL}
    overlap = sorted(m for m in found if m in programmed and m not in c.avoid and m not in c.allow)
    if overlap:
        out.append(Finding("overlap", "uses " + ", ".join(f"{LABEL.get(m, m)} (programmed {programmed[m]})"
                                                           for m in overlap)))

    # strength and metcon distinct
    strength_moves = set().union(*(movements_in(s.body) for s in by.get("strength", []))) if by.get("strength") else set()
    metcon_moves = set().union(*(movements_in(s.body) for s in by.get("metcon", []))) if by.get("metcon") else set()
    shared = sorted((strength_moves & metcon_moves) - MONOSTRUCTURAL - {"pull_apart"})
    if shared:
        out.append(Finding("distinct", "in both the strength block and the metcon: " + ", ".join(LABEL.get(m, m) for m in shared)))

    # the calorie line
    if c.week_remaining:
        want = f"{c.week_remaining:,}"
        if want not in (text or "") and str(c.week_remaining) not in (text or ""):
            out.append(Finding("calories", f"the week's remaining {want} kcal is not stated"))
    if not _KCAL_NUM_RE.search(text or ""):
        out.append(Finding("calories", "no estimated kcal for the session"))
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
             "loads": "loads", "duration": "the length", "excluded": "a movement you ruled out",
             "overlap": "tomorrow's gym movements", "distinct": "a movement repeats", "calories": "the calorie line"}
    seen: list[str] = []
    for f in findings:
        k = kinds.get(f.id, f.id)
        if k not in seen:
            seen.append(k)
    return "⚠️ Not verified: " + "; ".join(seen) + ". Check these before you start."


__all__ = ["MOVEMENTS", "MONOSTRUCTURAL", "HANGING", "LABEL", "Constraints", "Finding", "Section", "movements_in",
           "read_request", "profile_exclusions", "sections", "verify", "repair_note", "unverified_line"]
