# Two-Week Kickoff — Foundations Before Paternity Ends

**Window:** Wed May 6 → Sun May 17, 2026
**Constraint:** Newborn + toddler. Assume 30–60 min focus blocks, often interrupted.
**Goal:** Lock in the foundations so the campaign runs on autopilot once work resumes May 18.

> Rule of the two weeks: **No new agent code. No essay drafting beyond an outline. Foundations only.** Everything here is reversible-cheap if you defer it, but compounds if you don't.

---

## This week (Wed May 6 → Sun May 10) — 5 days

The theme is **identity and surface**. Get the proof points clean, get the domain live, get a tracker.

### Wed (today) — 30 min · Identity check
- [ ] Check availability: `modernthinker.build`, `modernthinker.dev`, `rahatplane.com`. Pick one. Buy it. (Namecheap or Cloudflare Registrar — under $20.)
- [ ] Verify Twitter/X handle availability: `@modernthinker`, `@modernthinkerbuilds`, `@venkat_sadras`. Reserve the best one even if you don't use it yet.
- [ ] Same on GitHub if `modernthinkerbuilds` isn't already pointing at a clean profile bio. (It is — verify.)

**Defer if:** new domain feels like decision-fatigue. Just secure handles today.

### Thu May 7 — 45 min · README top-30-lines
- [ ] Open [README.md](./README.md). Rewrite the first 30 lines so the *one-sentence pitch* leads, not "the vision."
- [ ] Pitch should answer: what is Rahat in one line, who is it for, and what's the proof. Today the README leads with vision; flip it so the proof leads.
- [ ] Suggested lead: *"Rahat is a sovereign, local-first multi-agent runtime running on a single Mac Mini, coordinating personal-AI agents through a shared SQLite intent ledger. Production for ~6 months, 330-case eval suite, ARB-grade architecture doc."*
- [ ] Commit: `docs(readme): lead with proof, not vision`

**Defer if:** you can't get a focused 45 min. Move to Fri.

### Fri May 8 — 60 min · ARCHITECTURE.md polish
- [ ] Fix Markdown rendering issues: escaped underscores in Section 1 ("v1.0 \(May 2026\)"), table alignment, code block fencing in §5.2.
- [ ] Tighten §1 Executive Summary to **5 bullets max** — currently 7. Each bullet starts with a noun, not a verb.
- [ ] Verify the three-plane ASCII diagram in §4 renders cleanly on GitHub (no half-em spacing collapse).
- [ ] Commit: `docs(architecture): formatting pass + tightened exec summary`

**Defer if:** the formatting hunt feels rabbit-holey after 30 min. Cap it. The doc is already 95% there.

### Sat May 9 — 90 min · Site live (one page, static)
- [ ] Stand up the chosen domain (e.g. `modernthinker.build`) on Cloudflare Pages or GitHub Pages. **One page, no JS framework.** Just HTML + minimal CSS.
- [ ] Page contains: name, one-sentence positioning, links to (a) `Rahat-Plane` repo, (b) `ARCHITECTURE.md`, (c) email contact, (d) `linkedin.com/in/venkatsadras`. Nothing else.
- [ ] No "essays" section, no "blog" link, no newsletter signup. The site is a business card, not a magazine.
- [ ] Commit the source to a `modernthinker-site` repo so it lives next to your other public work.

**Defer if:** can't get 90 min uninterrupted. Move to Sun. The site is the most leverage of the week — don't skip it, just shift it.

**One-page template** (drop this in `index.html` — done):
```html
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>Venkat Sadras</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:16px/1.6 ui-serif,Georgia,serif;max-width:42rem;margin:5rem auto;padding:0 1.25rem;color:#222}a{color:#0b6}h1{font-size:1.4rem;margin-bottom:.25rem}.muted{color:#777;font-size:.95rem}</style>
</head><body>
<h1>Venkat Sadras</h1>
<p class="muted">Product · agent runtimes · Bay Area</p>
<p>I'm building <a href="https://github.com/modernthinkerbuilds/Rahat-Plane">Rahat</a> — a sovereign, local-first multi-agent runtime running on a single Mac Mini.</p>
<p>Read the <a href="https://github.com/modernthinkerbuilds/Rahat-Plane/blob/main/ARCHITECTURE.md">architecture doc</a>.</p>
<p class="muted">Reach me: <a href="mailto:modernthinkerbuilds@gmail.com">modernthinkerbuilds@gmail.com</a> · <a href="https://www.linkedin.com/in/venkatsadras">LinkedIn</a></p>
</body></html>
```

### Sun May 10 — 60 min · Tracker
- [ ] Create one tracker doc — Notion page, Apple Notes, or `~/developer/agency/rahat/TRACKER.md`. Whichever you'll actually open.
- [ ] Five sections only:
  1. **Pipeline** — target companies × {watching / contacted / convo / advanced}
  2. **Content backlog** — essays in outline / draft / shipped
  3. **Outreach** — podcast targets, status, last-touch date
  4. **KPI snapshot** — followers, repo stars, inbound DMs (update monthly only)
  5. **Weekly log** — 3 bullets every Sunday: shipped / learned / next
- [ ] First entry: this Sunday. 3 bullets. Done.

**Defer if:** energy gone. Move to next Sunday. The tracker is internal and can wait one week.

---

## Next week (Mon May 11 → Sun May 17) — last paternity week

Theme is **set the rhythm**. By Sunday May 17 you should have: a writing slot in the calendar, a Twitter/X profile that does the talking, an Essay 1 outline, and a `BUILDLOG.md` started in the repo.

### Mon May 11 — 30 min · Twitter/X profile
- [ ] Profile photo: clean headshot or tasteful avatar.
- [ ] Header image: optional — a screenshot of the three-plane diagram works.
- [ ] Bio: *"Building Rahat — a sovereign agent runtime. PM. modernthinker.build"* (under 160 chars).
- [ ] Pinned tweet: link to the ARCHITECTURE.md with one sentence. *"I wrote up the architecture of the multi-agent runtime I've been building on a Mac Mini."* Done.
- [ ] **Follow rule:** today, follow 25 people. Not a curated list — just start with @swyx, @sama, @karpathy, @simonw, @hwchase17, @nathanlambert, @jxnlco, @AnthropicAI, @soumithchintala, plus 16 more in agent infra you genuinely respect. The rest comes naturally.

### Tue May 12 — 45 min · LinkedIn + cold-storage profile setup
- [ ] LinkedIn headline rewrite: *"Product @ \[Company\] · Building Rahat, a sovereign agent runtime"*. Under 220 chars.
- [ ] About section: 3 short paragraphs. (1) Who you are. (2) What Rahat is and link. (3) What you care about: agent runtimes, control planes, evals as specs. No buzzwords.
- [ ] Add Rahat as a "project" with the GitHub link.
- [ ] **Don't post anything yet.** LinkedIn is a recruiter signal channel, not a broadcast channel — at least not until essay 1 ships.

### Wed May 13 — 60 min · Essay 1 outline
- [ ] Open a new file: `~/developer/agency/rahat/essays/01-agents-are-roles.md` (create the directory).
- [ ] Three-section outline:
  1. The thesis in one paragraph: *"Agents are roles, not models — the interesting work is the runtime."*
  2. Three supporting points, each with a Rahat-anchored proof: (a) deterministic core / LLM at edges, (b) state-bus over RPC, (c) Charter as policy chokepoint.
  3. The cost: what we gave up to keep the moat (single-machine availability, SQLite single-writer, no cloud failover).
- [ ] Write the **opening paragraph only**. 80–120 words. The rest waits.
- [ ] Commit privately to the repo (it's fine — repo is public, but most people won't notice an `essays/` folder until you announce).

### Thu May 14 — 30 min · BUILDLOG.md, entry zero
- [ ] Create `BUILDLOG.md` at repo root.
- [ ] Format: H2 = ISO week (e.g. `## 2026-W19`). Three bullets: **shipped**, **learned**, **next**.
- [ ] First entry covers today + last week. Honest, terse, dated.
- [ ] Commit: `docs(buildlog): start the public log`. **This is the start of the cadence.**

### Fri May 15 — 30 min · Audit
- [ ] Walk through every public surface in 5 minutes each:
  - GitHub profile bio — does it match the positioning?
  - Pinned repo on GitHub profile — should be `Rahat-Plane`.
  - Site — does it load fast on mobile?
  - Twitter — is the bio crisp?
  - LinkedIn headline — is it accurate?
- [ ] Fix the worst one. Leave the rest.

### Sat May 16 — 90 min · Locked weekly slot
- [ ] **The single most important task of the two weeks:** put a recurring 90-min "Rahat writing" calendar block in your calendar. Recommended: Sat 6:30–8:00am or Sun 9:30–11:00pm. Pick the one your household actually allows.
- [ ] Use this Sat's block to expand Essay 1 outline → first draft of section 1.
- [ ] No publishing target. Just the slot existing is the win.

### Sun May 17 — 30 min · Week-2 retrospective
- [ ] Tracker → weekly log entry. 3 bullets:
  - shipped this week
  - learned this week
  - **the one foundation I still need to lock before paternity ends.**
- [ ] If energy permits, write a single tweet (don't post): *"Spent the last two weeks setting up foundations for a thing I've been quietly building. Architecture doc is in the README. Bajrangi ships next."* — save it as a draft. You'll publish later.

---

## What you will have at end of week 2

1. A domain that points at a one-page site.
2. A tightened README and ARCHITECTURE.md.
3. A clean Twitter/X profile + LinkedIn headline.
4. An essay folder with a real outline + opening paragraph for Essay 1.
5. A `BUILDLOG.md` started — the public cadence ledger.
6. A 90-min weekly writing slot on the calendar.
7. A tracker that takes 5 minutes to update on Sundays.

That's it. **Nothing has been published. Nothing has been broadcast.** The proof points are clean and the system is set up to run async after May 18.

---

## What you are explicitly NOT doing in these two weeks

- ❌ Writing a full essay.
- ❌ Reaching out to podcasts.
- ❌ Tweeting your own takes.
- ❌ Building a new agent in Rahat.
- ❌ Designing a logo or brand mark.
- ❌ Setting up Substack or any newsletter platform.
- ❌ Filming a video.

Each of these will be tempting. Each is a Phase 1+ task in the main [CAMPAIGN_PLAN.md](./CAMPAIGN_PLAN.md). Resist.

---

## If a day collapses

You will lose at least one day to baby reality. That's fine.

**Triage rule:** if you only have one hour total in week 1, spend it on **Sat May 9 — site live**. If you only have one hour in week 2, spend it on **Sat May 16 — calendar block**. The rest is recoverable; those two are not.

---

## After May 18

The main [CAMPAIGN_PLAN.md](./CAMPAIGN_PLAN.md) Phase 1 starts on its own schedule. Your weekly Sat 90-min slot becomes the heartbeat. The publish-essay-1 trigger is "Bajrangi shipped + 3 essays in the bank" — likely month 7–8. Until then: build, log, listen.

— end —
