"""fraser.source — SugarWOD adapter into the substrate.

Per spec §11.5, the gym's SugarWOD calendar is the source of truth for
the day's programming; Fraser's job is to *adapt* (not invent) those
workouts. This module owns the substrate adapter layer:

    1. Read the JSON archive `bridges/sugarwod/server.py` writes to
       `staging/workspace/gym-programming/archive/sugarwod.*.json`.
    2. Parse each day's `workouts[]` (title + description) into
       structured `ParsedSection` / `ParsedMovement` rows.
    3. Persist as `fraser_source_workout` entities — one per day,
       keyed on `date_int`, idempotent on re-ingest.
    4. Apply Kobe's `BLACKLIST` / `STRENGTH_BLACKLIST` / `SOFT_BLACKLIST`
       / `SKIP_SECTION_TITLES` as filters during parse. Constants
       imported DIRECTLY from `agents.the_scientist.protocols` — these
       are declarative, no substrate-symmetric pattern needed (see
       Day-5 directive's "Kobe blacklist" decision).

This is the 6th file in the agent pattern — adapter layer, distinct
from the five canonical files (protocols/state/tools/handler/main).
Documented in ADR-004's status table.

Doctrine pins:
    • No silent fallbacks. Past incidents (DOM rename, "MON"/"Mon"
      case bug) silently broke for weeks. Parse failures surface as
      explicit entity-body `parse_errors` lists, not as None.
    • Store BOTH raw + parsed. When the parser improves, reparse from
      `workouts_raw` without losing data.
    • Idempotent on `date_int`. Re-ingest replaces, doesn't duplicate.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.fraser.protocols import (  # noqa: E402
    AGENT, ENTITY_SOURCE_WORKOUT,
    FraserSourceWorkoutBody, ParsedWorkout, ParsedSection, ParsedMovement,
    normalize_movement,
)
from core import memory as _mem_raw  # noqa: E402
from core.memory import api as _mem_api  # noqa: E402

# Kobe blacklist constants — direct import per Day-5 directive. These
# are declarative (no time / governance dimension) so the substrate-
# symmetric `cross_agent_list` pattern does not apply.
from agents.the_scientist.protocols import (  # noqa: E402
    BLACKLIST as _KOBE_BLACKLIST,
    STRENGTH_BLACKLIST as _KOBE_STRENGTH_BLACKLIST,
    SOFT_BLACKLIST as _KOBE_SOFT_BLACKLIST,
    SKIP_SECTION_TITLES as _KOBE_SKIP_SECTION_TITLES,
)


# ─────────────────────────── Archive discovery ────────────────────────
DEFAULT_ARCHIVE_DIR = Path(_REPO_ROOT) / "staging" / "workspace" / "gym-programming" / "archive"


def find_latest_archive(archive_dir: Path | None = None) -> Path | None:
    """Return the most recently-written `sugarwod.<week>.<ts>.json`
    archive, or None if the directory is empty / missing. Mtime-based,
    not filename-based — handles re-scrapes within a week correctly."""
    d = archive_dir or DEFAULT_ARCHIVE_DIR
    if not d.exists():
        return None
    candidates = list(d.glob("sugarwod.*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ─────────────────────────── Format / cap parsers ─────────────────────
# Patterns documented in §11.5: SugarWOD uses consistent prose forms
# inside `description`. These regexes target the canonical shapes.

_FORMAT_PATTERNS = [
    (re.compile(r"\bFor\s+Time\b", re.I),       "For Time"),
    (re.compile(r"\bAMRAP\s*\d*", re.I),         "AMRAP"),
    (re.compile(r"\bEMOM\s*\d*", re.I),          "EMOM"),
    (re.compile(r"\bFor\s+Quality\b", re.I),     "For Quality"),
    (re.compile(r"\bFor\s+Reps\b", re.I),        "For Reps"),
    (re.compile(r"\bTabata\b", re.I),            "Tabata"),
    (re.compile(r"Every\s+\d+:\d{2}\s*[x×]\s*\d+\s*Sets?", re.I), "Every X:XX x N Sets"),
]

# Cap-min extraction: "AMRAP 15", "AMRAP 15:00", "15:00 AMRAP", "Cap 20"
_CAP_PATTERNS = [
    re.compile(r"\bAMRAP\s+(\d+)\b", re.I),
    re.compile(r"\b(\d+):00\s+AMRAP\b", re.I),
    re.compile(r"\bEMOM\s+(\d+)\b", re.I),
    re.compile(r"\bCap\s*[:=]?\s*(\d+)", re.I),
    re.compile(r"\b(\d+)[:-]?\s*min(?:ute)?\s+(?:cap|AMRAP|EMOM)", re.I),
]

# Sets/rounds: "21-15-9", "5 Rounds", "3 Sets", "Every 4:00 x 4 Sets"
_REP_SCHEME_RE   = re.compile(r"\b(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\b")
_ROUNDS_RE       = re.compile(r"\b(\d+)\s+(?:rounds?|RFT)\b", re.I)
_SETS_RE         = re.compile(r"\b(\d+)\s+sets?\b", re.I)
_EVERY_SETS_RE   = re.compile(r"Every\s+(\d+):(\d{2})\s*[x×]\s*(\d+)\s+Sets?", re.I)


def _detect_format(text: str) -> str:
    """First pattern match wins. Returns empty string if none match —
    the parser surfaces that explicitly rather than guessing."""
    for pat, name in _FORMAT_PATTERNS:
        if pat.search(text):
            return name
    return ""


def _detect_cap_min(text: str, fmt: str) -> int:
    """Try each cap pattern in order; first numeric match wins.

    For 'Every X:XX x N Sets' the cap is X * N minutes (e.g., 4:00 x
    4 = 16 min). For other formats, look for an explicit cap token.
    Returns 0 when no cap is detected — `For Quality` work and
    accessory sections legitimately have no cap.
    """
    if fmt == "Every X:XX x N Sets":
        m = _EVERY_SETS_RE.search(text)
        if m:
            minutes, _seconds, sets = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return minutes * sets
    for pat in _CAP_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    return 0


def _detect_rounds_or_structure(text: str, fmt: str) -> str:
    """Extract a short structure tag: '21-15-9', '5 RFT', 'AMRAP 18',
    'EMOM 12', 'Every 4:00 x 4 Sets'. Returns the format itself if
    nothing more specific shows up."""
    m = _EVERY_SETS_RE.search(text)
    if m:
        return f"Every {m.group(1)}:{m.group(2)} x {m.group(3)} Sets"
    m = _REP_SCHEME_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _ROUNDS_RE.search(text)
    if m:
        return f"{m.group(1)} Rounds"
    if fmt in ("AMRAP", "EMOM"):
        for pat in _CAP_PATTERNS[:3]:
            mm = pat.search(text)
            if mm:
                return f"{fmt} {mm.group(1)}"
    return fmt


# ─────────────────────────── Movement extraction ──────────────────────
# Match a "<reps> <movement>" line, where reps may be "12", "15/11",
# "400m", "1:00", "21-15-9", "12/9 Calorie Echo Bike", or absent.
# We strip the rep-prefix and run the remainder through
# `normalize_movement` for canonicalization.

# Rep prefix at the start of a line we'll lift out. The wider regex
# tolerates SugarWOD's slash variants ("15/11 Calorie Echo Bike"),
# distance suffixes ("400m"), minute-second time forms ("1:00"),
# and PRVN Reset's per-side patterns ("6/side …", ":45/side …").
_REP_PREFIX_RE = re.compile(
    r"""^[\-\*•]*\s*           # bullet / dash
        (:?\d+(?::\d{2})?(?:[/-]\d+)?  # 12 | 15/11 | 21-15-9 | 1:00 | :45
            (?:/side|\s*per\s*side)?    # optional /side qualifier
            \s*(?:cal(?:orie)?s?|m|min|ft|sec|s|reps?)?
        )                              # optional unit
        \s+                            # gap before movement
        (.*)$                          # the movement itself
    """, re.IGNORECASE | re.VERBOSE)

# Meta-names that look like movements but aren't — block headers
# like "6 Rounds:" / "3 Sets:" lift a number out and parse the rest
# as "rounds:" / "sets:". Filter at the end of the extract pipeline.
_NON_MOVEMENT_NAMES = frozenset({
    "rounds", "round", "sets", "set", "reps", "rep",
})

# Load lines like "Kettlebell: 53/35lb, 24/16kg" or "Barbell: 135/95lb"
# attach to the most-recent movement (or to the section as a whole if
# no movement has been seen yet).
_LOAD_LINE_RE = re.compile(
    r"""^(?P<equip>kettlebell|barbell|dumbbells?|wall\s*ball|box)\s*:\s*
        (?P<load>.+)$
    """, re.IGNORECASE | re.VERBOSE)

# Scaling-tier headers: "Level 2:", "Level 1:", "Masters 55+:",
# "Competitor:", "Hotel Gym / Travel:", "Rx:"
_TIER_HEADER_RE = re.compile(
    r"^(level\s*\d+|masters?\s*\d*\+?|competitor|hotel\s*gym|rx|scaled)\s*[/]?[a-z\s]*:",
    re.IGNORECASE)


def _extract_movements(description: str) -> list[ParsedMovement]:
    """Pull (reps_or_time, movement) pairs out of a section's raw text.

    Best-effort. Handles the common SugarWOD shapes from §11.5's
    sample (Snatch Complex / Pikachu's Thunderbolt / PRVN Reset /
    Optional Accessories). Doesn't try to be exhaustive — the LLM
    sees the raw_description and can pick up what regex misses.

    Skips lines that are tier headers, "Score = …", "Intent: …", or
    "then" / blank. Loads attach to the most-recent movement.
    """
    movements: list[ParsedMovement] = []
    if not description:
        return movements

    for raw_line in description.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()

        # Skip meta / scaffolding lines.
        if (low.startswith("score")
                or low.startswith("intent")
                or low.startswith("then")
                or low == "rx"
                or "use this to confirm" in low
                or "watch the video" in low):
            continue
        if _TIER_HEADER_RE.match(line):
            continue
        # Section-header lines that end in a colon ("6 Rounds:",
        # "3 Sets:", "For Time:") are structural metadata, not
        # movements. They'd otherwise sneak through `_REP_PREFIX_RE`
        # with name="rounds:"/"sets:". (Day-5 demo bug.)
        if line.rstrip().endswith(":"):
            continue

        # Load attachment.
        m = _LOAD_LINE_RE.match(line)
        if m:
            load = f"{m.group('equip').strip()}: {m.group('load').strip()}"
            if movements:
                # Attach to most recent movement that doesn't already
                # have a load.
                for mov in reversed(movements):
                    if not mov.load_text:
                        mov.load_text = load
                        break
                else:
                    movements[-1].load_text = load
            continue

        # Try the rep-prefix pattern.
        m = _REP_PREFIX_RE.match(line)
        if m:
            reps = m.group(1).strip()
            name_raw = m.group(2).strip()
            # Order matters: markdown link `[Text](url)` BEFORE the
            # trailing-parens strip, otherwise `\(.*\)$` eats the url
            # half and leaves a stray `[Text]`. (Day-6 demo bug.)
            name_raw = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", name_raw)
            # Strip leftover trailing parenthetical (e.g. "(3 sec hold)").
            name_raw = re.sub(r"\s*\(.*\)\s*$", "", name_raw)
            # Drop "@ working load" / "@ 70%" suffixes — those go into
            # load_text, not the movement name.
            name_raw = re.sub(r"\s+@\s+.*$", "", name_raw)
            # Strip stray brackets if any markdown form sneaks through.
            name_raw = name_raw.strip().strip("[]")
            name = normalize_movement(name_raw)
            # Block meta-names that slipped through (Day-5 demo bug:
            # "6 Rounds:" producing name="rounds:").
            if not name or name in _NON_MOVEMENT_NAMES:
                continue
            movements.append(ParsedMovement(
                name=name,
                reps_or_time=reps,
                load_text="",
                raw_text=raw_line,
            ))
            continue

        # Lines that name a movement without a leading rep count
        # (e.g., "1 Hang Snatch + 1 Low Hang Snatch" already covered;
        # standalone-name lines like "Bench Press 5-3-2-2-Max" are
        # parsed for their reps via the rep-scheme regex above).

    return movements


def _classify_section(title: str) -> str:
    """Classify a section by title. Match SugarWOD's conventional
    title patterns:
        - "PRVN Reset", "Restorative Flow"     → reset
        - "Optional Accessories", "Optional"   → accessory
        - "Specific Prep", "Primer", "Warm-Up" → prep
        - "[<Name>: Levels]"                   → levels
        - "<Bench/Snatch/Deadlift> Complex"    → strength
        - "Bench Press <reps>"                 → strength
        - Bare named WOD ("Annie", "\"Pikachu's…\"") → wod
    """
    t = title.lower()
    if "prvn" in t or "reset" in t or "restor" in t:
        return "reset"
    if "optional" in t or "accessor" in t:
        return "accessory"
    if "specific prep" in t or "primer" in t or "warm-up" in t or "warm up" in t:
        return "prep"
    if t.startswith("[") and "levels" in t:
        return "levels"
    if any(s in t for s in ("complex", "press 5-", "deadlift 5-",
                            "squat 5-", "clean 5-", "snatch 5-",
                            "strength:", "press 3-", "deadlift 3-",
                            "squat 3-")):
        return "strength"
    return "wod"


def _check_blacklist(title: str, description: str) -> tuple[bool, str, bool]:
    """Return (is_blacklisted, reason, is_skip_section).

    Skip-section check goes FIRST: if the title matches a Kobe
    SKIP_SECTION_TITLES entry, the section is exempt from blacklist
    enforcement (optional / accessory work is opt-in).
    """
    t_low = title.lower()
    is_skip = any(skip in t_low for skip in _KOBE_SKIP_SECTION_TITLES)
    if is_skip:
        return False, "", True
    text = f"{title}\n{description}".lower()
    for term in _KOBE_BLACKLIST:
        if term in text:
            return True, f"hard-blacklist:{term}", False
    for term in _KOBE_STRENGTH_BLACKLIST:
        if term in text:
            return True, f"strength-blacklist:{term}", False
    for term in _KOBE_SOFT_BLACKLIST:
        if term in text:
            return True, f"soft-blacklist:{term}", False
    return False, "", False


# ─────────────────────────── Public parse helpers ─────────────────────
def parse_source_workout(description: str, title: str) -> ParsedSection:
    """Parse one section (one entry in `day.workouts[]`). Returns a
    `ParsedSection` populated with format, cap, structure, movements,
    and blacklist flags. The raw `description` is preserved on the
    section so a parser improvement can reparse without re-fetching.
    """
    desc = description or ""
    fmt = _detect_format(desc)
    cap = _detect_cap_min(desc, fmt)
    structure = _detect_rounds_or_structure(desc, fmt)
    kind = _classify_section(title or "")
    is_blk, reason, is_skip = _check_blacklist(title or "", desc)
    movements = _extract_movements(desc)
    return ParsedSection(
        title=title or "",
        section_kind=kind,
        format=fmt,
        cap_min=cap,
        rounds_or_structure=structure,
        movements=movements,
        raw_description=desc,
        is_blacklisted=is_blk,
        blacklist_reason=reason,
        is_skip_section=is_skip,
    )


# Rest-day title patterns (both spec'd shapes).
_REST_TITLE_RE = re.compile(
    r"^(rest\s*day|active\s*recovery|recovery\s*day|off\s*day)$",
    re.IGNORECASE)


def parse_day(day_dict: dict) -> ParsedWorkout:
    """Parse one day. Detects rest-day shapes (both empty workouts
    array AND placeholder-title workout). Picks a `primary_wod_index`
    by scanning for the named-WOD section (the one whose title is
    quoted, or whose section_kind is 'wod' and isn't 'levels')."""
    date_int = str(day_dict.get("date_int", ""))
    header = str(day_dict.get("header", ""))
    workouts = day_dict.get("workouts", []) or []

    # Rest-day shape #1: empty workouts array.
    if not workouts:
        return ParsedWorkout(
            date_int=date_int, header=header,
            is_rest_day=True, rest_day_label="Rest Day",
            sections=[], primary_wod_index=-1,
            blacklisted_section_count=0,
        )

    # Rest-day shape #2: single placeholder workout.
    if len(workouts) == 1:
        only = workouts[0] or {}
        title = (only.get("title") or "").strip()
        desc = (only.get("description") or "").strip()
        if _REST_TITLE_RE.match(title) and not desc:
            return ParsedWorkout(
                date_int=date_int, header=header,
                is_rest_day=True, rest_day_label=title,
                sections=[], primary_wod_index=-1,
                blacklisted_section_count=0,
            )

    sections: list[ParsedSection] = []
    blk_count = 0
    primary_idx = -1

    for i, w in enumerate(workouts):
        sec = parse_source_workout(
            description=str(w.get("description") or ""),
            title=str(w.get("title") or ""))
        sections.append(sec)
        if sec.is_blacklisted and not sec.is_skip_section:
            blk_count += 1
        # Primary WOD: first non-prep, non-levels, non-reset, non-
        # accessory section. Prefer a section_kind=='wod' (the named
        # WOD); fall back to 'strength' if no 'wod' fires.
        if primary_idx == -1:
            if sec.section_kind == "wod" and not sec.is_skip_section:
                primary_idx = i
    if primary_idx == -1:
        for i, sec in enumerate(sections):
            if sec.section_kind == "strength" and not sec.is_skip_section:
                primary_idx = i
                break

    return ParsedWorkout(
        date_int=date_int, header=header,
        is_rest_day=False, rest_day_label="",
        sections=sections,
        primary_wod_index=primary_idx,
        blacklisted_section_count=blk_count,
    )


# ─────────────────────────── Ingestion ────────────────────────────────
def ingest_source_week(json_path: Path | str,
                       *, db_path: str | None = None) -> int:
    """Read the JSON archive, parse each day, persist as
    `fraser_source_workout` entities. Returns the count of entities
    written (one per day, INCLUDING rest days — the rest-day entity
    is how `get_todays_source_workout` knows today is a rest day).

    Idempotent on `date_int`: re-ingest of the same week with newer
    parsing supersedes prior rows for the same date.

    Raises `ValueError` if the JSON shape is malformed — better to
    surface explicitly than to write half-parsed garbage.
    """
    path = Path(json_path)
    raw = json.loads(path.read_text())

    if not isinstance(raw, dict) or "days" not in raw:
        raise ValueError(
            f"{path}: archive must be a dict with a 'days' key")
    days = raw.get("days") or []
    fetched_at = str(raw.get("fetched_at") or "")
    gym_program_name = str(raw.get("url") or "").split("track=")[-1] or "workout-of-the-day"

    n = 0
    for day in days:
        if not isinstance(day, dict):
            continue
        date_int = str(day.get("date_int") or "")
        if not date_int:
            continue
        parsed = parse_day(day)
        body = FraserSourceWorkoutBody(
            date_int=date_int,
            header=str(day.get("header") or ""),
            fetched_at_iso=fetched_at,
            gym_program_name=gym_program_name,
            ingestion_method="sugarwod_bookmarklet",
            workouts_raw=list(day.get("workouts") or []),
            parsed=parsed,
        )

        # Idempotent: supersede any prior entity for this date_int.
        prior_id = _find_active_source_for_date(date_int, db_path=db_path)
        if prior_id is not None:
            body.supersedes_entity_id = prior_id
            _mem_api.goal_supersede(
                prior_id,
                reason=f"re-ingest sugarwod week {raw.get('week_start')}",
                db_path=db_path)

        _mem_api.goal_create(
            AGENT, type=ENTITY_SOURCE_WORKOUT,
            payload=body.to_payload(),
            rationale=f"sugarwod ingest {raw.get('week_start')}",
            supersede_existing=False,
            db_path=db_path)
        n += 1
    return n


def ingest_latest_source_week(*,
                              archive_dir: Path | None = None,
                              db_path: str | None = None) -> tuple[int, Path | None]:
    """Find the most-recent archive file and ingest it. Returns
    `(count, path)` so the caller can log which file was processed.
    Returns `(0, None)` if no archive exists — caller can surface
    'no SugarWOD data yet' explicitly rather than masking with 0."""
    path = find_latest_archive(archive_dir=archive_dir)
    if path is None:
        return 0, None
    return ingest_source_week(path, db_path=db_path), path


def _find_active_source_for_date(date_int: str,
                                 *, db_path: str | None = None) -> int | None:
    """Look up the active `fraser_source_workout` entity_id for the
    given `date_int`. Used by ingest for idempotent supersession."""
    rows = _mem_raw.list_entities(
        agent=AGENT, type=ENTITY_SOURCE_WORKOUT,
        status="active", include_expired=True, limit=200,
        db_path=db_path)
    for r in rows:
        p = r.get("payload") or {}
        if p.get("date_int") == date_int:
            return r.get("entity_id")
    return None


__all__ = [
    "DEFAULT_ARCHIVE_DIR",
    "find_latest_archive",
    "parse_source_workout",
    "parse_day",
    "ingest_source_week",
    "ingest_latest_source_week",
]
