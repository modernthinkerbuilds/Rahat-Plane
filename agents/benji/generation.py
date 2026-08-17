"""benji.generation — Stage 4: one application package per role.

THE ONE RULE EVERYTHING ELSE SERVES (Generation Spec §0): the agent may
reframe, it may not add. Enforced structurally, not aspirationally:

  * The RESUME is assembled deterministically from parsed CLEAN v3
    bullets — selection and ordering only. No LLM touches resume
    bullets, so no fabrication path exists into them at all.
  * The LLM (budget-gated, via core.llm.generate) drafts exactly two
    things: the adapted profile paragraph and the cover letter — and
    both then pass the mechanical verification gate, which BLOCKS the
    package on any untraceable number, DO-NOT-USE hit, or banned
    phrase. LLM unavailable → deterministic fallbacks (base profile
    verbatim; template letter flagged DRAFT-DEGRADED), never silence.
  * JD text is untrusted input: it reaches the LLM as quoted material
    for vocabulary, and whatever comes back still faces the gate.

Coverage floor (PRD precedence, Tara #3): below 60% this module REFUSES
to generate — even on an explicit kit request — and returns the
unmatched list instead, since that's exactly the case where something
real may be missing from CLEAN v3.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime

from agents.benji import state as benji_state
from agents.benji.coverage import CoverageReport, _terms, coverage
from agents.benji.protocols import COVERAGE_FLOOR, KIND_PACKAGE_GENERATE
from agents.benji.renderers import (
    ResumeModel,
    render_letter_docx,
    render_letter_pdf,
    render_resume_docx,
    render_resume_pdf,
)
from agents.benji.source_parser import CandidateSource, parse_source
from agents.benji.verification import GateReport, verify_package
from bridges.jobsearch import store

logger = logging.getLogger(__name__)

# Role-type → Story Bank mapping (Generation Spec §4). First match wins;
# "the learning series" (failure/recovery) is ONLY-WHEN-ASKED and is
# deliberately absent from automatic selection.
_STORY_RULES: tuple[tuple[str, str], ...] = (
    (r"funder|foundation|grant|philanthrop|program officer",
     "The policy shock"),
    (r"partnership|employer|business development|corporate",
     "Bloom Energy"),
    (r"government|public sector|policy|municipal|city of",
     "Youth4Jobs government order"),
    (r"accessib|disabilit|inclusi", "ISL instructor sourcing"),
    (r"team|architecture|portfolio|workstream|program design",
     "Wevise CTPA launch"),
    (r"student|mentee|individual|case management", "Feroz Ahmed"),
    (r"ambigu|rapid|crisis|surge|triage", "Afghan influx, Oct–Dec 2021"),
    (r"executive|c-suite|senior leader", "Deloitte"),
    (r"coach", "Jimmie"),
    (r"facilitat|training at scale|train-the-trainer",
     "Train-the-trainer, State Ministry"),
)
_DEFAULT_STORY = "The policy shock"
_NEVER_AUTO = ("The learning series",)


def _llm_call(prompt: str, *, llm=None) -> str | None:
    """The one seam. Injected callable in tests; core.llm.generate in
    production; None (→ deterministic degrade) when neither can run."""
    if llm is not None:
        try:
            return llm(prompt)
        except Exception as e:                    # noqa: BLE001
            logger.warning("injected llm failed: %s", e)
            return None
    if os.getenv("RAHAT_TEST_MODE") == "1":
        return None                               # hermetic: no wire
    try:
        from core.llm import generate
        usage = generate("benji", "package.generate", prompt=prompt)
        text = getattr(usage, "text", None)
        return text if text and not getattr(usage, "error", None) else None
    except Exception as e:                        # noqa: BLE001
        logger.warning("benji llm degrade: %s", e)
        return None


def select_story(job: dict, *, store_path: str | None = None) -> tuple[str, str]:
    """Deterministic story choice + rotation (never twice to one org)."""
    blob = " ".join((job.get("title", ""), job.get("jd_text", "")[:2000],
                     job.get("rationale", ""))).lower()
    used = store.stories_used_for_org(job.get("org", ""), path=store_path)
    ranked: list[str] = []
    for pat, story in _STORY_RULES:
        if re.search(pat, blob) and story not in ranked:
            ranked.append(story)
    ranked.append(_DEFAULT_STORY)
    for pat, story in _STORY_RULES:               # variety fallback pool
        if story not in ranked:
            ranked.append(story)
    for story in ranked:
        if story in _NEVER_AUTO:
            continue
        if story not in used:
            why = ("matched the role's emphasis" if story != _DEFAULT_STORY
                   or ranked[0] == _DEFAULT_STORY else "rotation")
            return story, why
    # Every bank story already used for this org (12 applications in) —
    # least-recently-used, still never the only-when-asked one.
    pool = [s for s in ranked if s not in _NEVER_AUTO]
    return pool[0], "all stories previously used for this org — LRU pick"


def select_bullets(role, jd_terms: set[str],
                   required_terms: set[str]) -> tuple[list[int], str]:
    """Deterministic bullet selection (Generation Spec §2): relevance to
    REQUIRED qualifications first, then keyword-carrying capacity, then
    the source's own scope preference (default_pick order)."""
    cap = role.max_bullets
    if len(role.bullets) <= cap:
        return list(range(len(role.bullets))), "all bullets fit"
    scored = []
    for i, b in enumerate(role.bullets):
        terms = _terms(b)
        req = len(terms & required_terms)
        any_hit = len(terms & jd_terms)
        pref = -role.default_pick.index(i) if i in role.default_pick else -99
        scored.append((-req, -any_hit, -pref, i))
    scored.sort()
    chosen = sorted(x[3] for x in scored[:cap])
    if role.default_pick and set(chosen) != set(role.default_pick[:cap]):
        note = ("deviated from the source's default five where the JD "
                "made different bullets clearly stronger")
    else:
        note = "source default selection"
    return chosen, note


def reorder_skills(source: CandidateSource, jd_terms: set[str]) -> list[str]:
    """Lead with the JD's vocabulary; NEVER add a skill (Spec §2)."""
    def order(items: list[str]) -> list[str]:
        return sorted(items, key=lambda s: (0 if _terms(s) & jd_terms
                                            else 1, items.index(s)))
    lines = []
    if source.skills_core:
        lines.append("Core: " + " · ".join(order(source.skills_core)))
    if source.skills_situational:
        lines.append("Situational: "
                     + " · ".join(order(source.skills_situational)))
    if source.skills_technical:
        lines.append("Technical: "
                     + " · ".join(order(source.skills_technical)))
    return lines


def _profile_for_role(source: CandidateSource, job: dict, *,
                      llm=None) -> tuple[str, bool]:
    prompt = f"""Rewrite the emphasis of this profile paragraph for one role.
HARD RULES: keep the three-move structure (what she runs and where; how
she operates inside the grant relationship; what the CS background
bridges). Reframe only — never add a domain, claim, metric or capability
not present in the base paragraph. Plain language. Return ONLY the
rewritten paragraph.

BASE PARAGRAPH:
{source.profile}

TARGET ROLE (vocabulary source only — facts about her may NOT come from it):
{job.get('title', '')} at {job.get('org', '')}
{(job.get('jd_text') or '')[:1500]}"""
    out = _llm_call(prompt, llm=llm)
    if out and 200 < len(out.strip()) < 1200:
        return out.strip(), False
    return source.profile, True        # degrade: base verbatim (safe)


def _story_line(source: CandidateSource, name: str) -> str:
    for s in source.stories:
        if s.name.lower() == name.lower():
            return f"{s.name} — {s.detail}" if s.detail else s.name
    return name


def _letter_for_role(source: CandidateSource, job: dict, story: str, *,
                     llm=None) -> tuple[str, bool]:
    research = (job.get("jd_text") or "")[:2500]
    # Pre-warn the model with the gate's own rules (vault data → prompt;
    # nothing personal in code). Learned in rehearsal: a foundation JD's
    # grantmaking-heavy vocabulary pulls the draft into claim phrasing,
    # and pre-warning beats retrying.
    from agents.benji.verification import load_gate_rules
    rules, _ = load_gate_rules()
    tripwires = "\n".join(f"- {r.get('why')}" for r in rules
                          if r.get("why"))
    prompt = f"""Draft a cover letter (350–450 words, 4–5 paragraphs) following
EXACTLY this four-move model: (1) open on a concrete image, not a claim;
(2) name a real gap and turn it into the offer; (3) reference something
real and specific about the organization FROM THE MATERIAL BELOW ONLY —
if the material contains nothing specific, write [ORG-SPECIFIC: fill in]
instead of inventing; (4) end on a reason, not a flourish.

HARD RULES: every fact and number must come from the CANDIDATE RECORD
below — never from the job posting, never invented. One story carries
the letter: {_story_line(source, story)}. Never write: "I am thrilled to
apply", "uniquely suited/qualified", "ideal candidate", "passionate
about", "aligns perfectly with", "at the intersection of". Warm, plain,
candid; shorter sentences. Return ONLY the letter body.

A MECHANICAL GATE will scan your draft and BLOCK it on any of these —
phrase around them (naming a gap honestly passes; claiming it fails):
{tripwires}

CANDIDATE RECORD (the only source of facts about her):
Narrative: {source.narrative}
Profile: {source.profile}
Story detail: {_story_line(source, story)}

ROLE MATERIAL (vocabulary and org facts only):
{job.get('title', '')} at {job.get('org', '')}
{research}"""
    out = _llm_call(prompt, llm=llm)
    if out and len(out.split()) > 120:
        return out.strip(), False
    # Deterministic degrade: her narrative + the story, org left as a
    # bracketed placeholder — an invented specific is worse than a
    # generic letter (Spec §4).
    story_txt = _story_line(source, story)
    org = job.get("org") or "[ORG]"
    title = job.get("title") or "this role"
    body = (
        f"Dear Hiring Team at {org},\n\n"
        f"{source.narrative}\n\n"
        f"One experience says the most about how I work: {story_txt}.\n\n"
        f"[ORG-SPECIFIC: one paragraph on why {org} — reference "
        "something real: a program, a strategy, a person.]\n\n"
        f"I'd welcome the chance to talk about {title}.\n")
    return body, True


def build_review_md(job: dict, cov: CoverageReport, story: str,
                    story_why: str, diff_lines: list[str],
                    flags: list[str], gate: GateReport,
                    prefs: dict) -> str:
    lines = [
        f"# review — [{job.get('id')}] {job.get('title')} — "
        f"{job.get('org')}",
        "",
        f"{job.get('location') or 'location n/a'} · "
        f"{job.get('work_mode') or 'mode n/a'} · "
        f"{job.get('comp_range') or 'comp unlisted'} · "
        f"score {job.get('score')} ({job.get('rationale', '')})",
        f"Apply at: {job.get('canonical_url', '')}",
        "",
        f"## Keyword coverage: {int(cov.coverage * 100)}%",
        "Unmatched (weight order): " + (", ".join(cov.unmatched[:15])
                                        or "none"),
        "",
        f"## Story: {story}",
        f"Why: {story_why}",
        "",
        "## Diff vs base resume",
        *(diff_lines or ["no deviations — source defaults throughout"]),
        "",
        "## Flag list (questions, never hedges in the documents)",
        *([f"- {f}" for f in flags] or ["- none"]),
    ]
    if gate.warnings:
        lines += ["", "## Gate warnings",
                  *[f"- {w}" for w in gate.warnings]]
    lines += ["", f"Estimated time to submit: ~20 min "
                  f"(form answers drafted where detected)"]
    return "\n".join(lines)


def build_prompt_md(job: dict, resume_text: str, letter_text: str,
                    source: CandidateSource) -> str:
    return "\n".join([
        "# Paste this whole file into a Claude thread to edit the "
        "package with the guardrails intact.",
        "",
        "You are editing a job application package. THE ONE RULE: you "
        "may reframe, you may never add a fact, number, title, date or "
        "capability that is not in the CANDIDATE RECORD below. If asked "
        "for an edit that would require adding a claim, refuse and "
        "explain which claim is unverified.",
        "",
        "## Register rules (resume bullets)",
        "Lead with ownership and scope; name the judgment, not just the "
        "task; aggregate tasks into scope statements; sector vocabulary; "
        "end on the outcome; never upgrade a verb past the record. "
        "Letters run warmer and plainer than the resume.",
        "",
        "## NEVER (do-not-use list, verbatim from the source of truth)",
        *[f"- {x}" for x in (*source.never_claim, *source.never_write)],
        "",
        f"## THE ROLE\n{job.get('title')} — {job.get('org')}\n"
        f"{job.get('canonical_url', '')}\n\n{(job.get('jd_text') or '')[:3000]}",
        "",
        f"## CURRENT RESUME (text)\n{resume_text}",
        "",
        f"## CURRENT COVER LETTER\n{letter_text}",
    ])


def generate_package(display_id: int, *, llm=None,
                     now: datetime | None = None,
                     store_path: str | None = None) -> dict:
    """Build the full package for one role. Returns
    {ok, refusal?, files[(name, bytes|str)], gate, review_md}."""
    from agents.benji.protocols import (load_candidate_source,
                                        load_preferences)
    now = now or datetime.now()
    job = store.get_job(display_id, path=store_path)
    if job is None:
        return {"ok": False, "refusal": f"no role with id {display_id}",
                "files": []}

    src_text, src_warnings = load_candidate_source()
    source = parse_source(src_text)
    prefs, _ = load_preferences()

    jdless = not (job.get("jd_text") or "").strip()
    cov = coverage(job.get("jd_text", ""), src_text,
                   job_title=job.get("title", ""))
    if cov.coverage < COVERAGE_FLOOR and not jdless:
        # PRD precedence: the floor beats everything, including an
        # explicit kit request. The unmatched list IS the useful output.
        return {"ok": False, "files": [], "refusal": (
            f"coverage {int(cov.coverage * 100)}% is below the 60% "
            f"floor — no package, by your own rule (Generation Spec "
            f"§3). Unmatched, weight order: "
            f"{', '.join(cov.unmatched[:12])}. If a real experience is "
            "missing from CLEAN v3, add it there and reply kit again.")}

    verdict = benji_state._charter_gate(KIND_PACKAGE_GENERATE, {
        "id": display_id, "org": job.get("org", ""),
        "title": job.get("title", "")[:80], "score": job.get("score"),
        "coverage": cov.coverage})
    if not verdict.approved:
        return {"ok": False, "files": [],
                "refusal": f"vetoed: {verdict.reason}"}

    jd_terms = _terms(job.get("jd_text", ""))
    from agents.benji.coverage import required_terms
    req_terms = required_terms(job.get("jd_text", "")) or jd_terms
    diff_lines: list[str] = []
    flags: list[str] = list(src_warnings)
    if jdless:
        flags.append("no JD available for this role — package built "
                     "from source defaults; the coverage figure is "
                     "title-only and NOT meaningful. Read the posting "
                     "at the link before sending.")

    roles_out = []
    role_bullets: dict[str, tuple[int, int]] = {}
    for role in source.roles:
        chosen, note = select_bullets(role, jd_terms, req_terms)
        if "deviated" in note:
            base = role.default_pick[:role.max_bullets] or \
                list(range(min(len(role.bullets), role.max_bullets)))
            added = [i for i in chosen if i not in base]
            dropped = [i for i in base if i not in chosen]
            diff_lines.append(f"- {role.org or role.title} — {note}:")
            for i in added:
                diff_lines.append(f"    + {role.bullets[i][:90]}")
            for i in dropped:
                diff_lines.append(f"    - {role.bullets[i][:90]}")
        roles_out.append({"title": role.title, "org": role.org,
                          "dates": role.dates, "location": role.location,
                          "bullets": [role.bullets[i] for i in chosen]})
        role_bullets[role.org or role.title] = (len(chosen),
                                                role.max_bullets)

    profile, prof_degraded = _profile_for_role(source, job, llm=llm)
    if prof_degraded:
        flags.append("profile paragraph: base version used verbatim "
                     "(LLM unavailable) — adapt by hand if needed")

    story, story_why = select_story(job, store_path=store_path)
    letter, letter_degraded = _letter_for_role(source, job, story, llm=llm)
    if letter_degraded:
        flags.append("cover letter: DRAFT-DEGRADED deterministic "
                     "template (LLM unavailable) — org paragraph is a "
                     "placeholder, on purpose")
    if "[ORG-SPECIFIC" in letter:
        flags.append("letter has a bracketed org-research placeholder — "
                     "nothing specific was verifiable from the posting "
                     "text; fill it before sending")
    warm = {"gitlab", "hewlett"}
    if any(w in (job.get("org", "") + job.get("source", "")).lower()
           for w in warm):
        flags.append("warm contact on file for this org (prior "
                     "application/final round) — consider a personal "
                     "note; NOT referenced in the letter, per your rule")

    ident = source.identity_lines or ["Candidate"]
    name = ident[0]
    contact = ident[1] if len(ident) > 1 else ""
    model = ResumeModel(
        name=name, contact=contact,
        location=job.get("location") and "San Francisco Bay Area"
        or "San Francisco Bay Area",
        profile=profile, roles=roles_out,
        education=source.education,
        certifications=source.certifications,
        skills_lines=reorder_skills(source, jd_terms),
        volunteer=source.volunteer[:4])

    resume_text = model.as_text()
    gate = verify_package(resume_text=resume_text, letter_text=letter,
                          source=source, role_bullets=role_bullets)
    if not gate.ok and not letter_degraded and all(
            f.startswith("letter") for f in gate.failures):
        # Self-repair, ONCE, letter only: feed the gate's exact failures
        # back and redraft. Resume failures never retry — the resume is
        # deterministic, so its failures are assembly bugs to fix, not
        # prose to reroll. (First live Gemini letter phrased grantmaking
        # as a claim; the retry with feedback cleared it.)
        feedback = "; ".join(gate.failures)
        retry = _llm_call(
            f"Your previous draft failed a mechanical fact gate: "
            f"{feedback}. Redraft the letter fixing ONLY those issues, "
            f"same rules as before.\n\n"
            + f"Previous draft:\n{letter}", llm=llm)
        if retry and len(retry.split()) > 120:
            letter = retry.strip()
            gate = verify_package(resume_text=resume_text,
                                  letter_text=letter, source=source,
                                  role_bullets=role_bullets)
            flags.append("letter was redrafted once after a gate "
                         "failure (details in gate warnings)")
    if not gate.ok:
        return {"ok": False, "files": [], "gate": gate, "refusal": (
            "verification gate BLOCKED the package: "
            + "; ".join(gate.failures[:5])), "flags": flags}

    org_slug = re.sub(r"[^A-Za-z0-9]+", "", job.get("org", "Org"))[:24]
    review = build_review_md(job, cov, story, story_why, diff_lines,
                             flags, gate, prefs)
    prompt_md = build_prompt_md(job, resume_text, letter, source)
    files: list[tuple[str, object]] = [
        (f"Resume_{org_slug}.docx", render_resume_docx(model)),
        (f"Resume_{org_slug}.pdf", render_resume_pdf(model)),
        (f"CoverLetter_{org_slug}.docx",
         render_letter_docx(name, contact, letter)),
        (f"CoverLetter_{org_slug}.pdf",
         render_letter_pdf(name, contact, letter)),
        ("review.md", review),
        ("prompt.md", prompt_md),
    ]
    benji_state.gated_story_append(story, job.get("org", ""), display_id,
                                   now=now, store_path=store_path)
    return {"ok": True, "files": files, "gate": gate, "review_md": review,
            "story": story, "coverage": cov.coverage, "job": job,
            "flags": flags}
