# Personal Positioning Campaign — "The Sovereign Agent"

**Subject:** Venkat Sadras
**Window:** May 2026 → May 2028 (12–24 months)
**Status:** v1.0 draft
**Anchors:** [ARCHITECTURE.md](./ARCHITECTURE.md), [README.md](./README.md), [`github.com/modernthinkerbuilds/Rahat-Plane`](https://github.com/modernthinkerbuilds/Rahat-Plane)

---

## 1. Campaign overview

**One-line summary.** A 24-month, evidence-led positioning campaign that turns Rahat — a real, shipped, ARB-grade multi-agent runtime — into the canonical proof point that Venkat is the rare PM who can both *think about* and *build* the agent layer, leading to a leadership hire at a model company, hyperscaler, or top agent startup.

**Primary objective.** Land a Director / Head of Product (Agents) role — or equivalent founding-PM seat — at one of: Anthropic, Google DeepMind / GCP Agent platform, OpenAI, a Tier-1 agent infra startup (Sierra, Adept-class, Lindy-class, vertical-agent leaders), by end of Q2 2028.

**Secondary objectives.**
- Become a recognized voice on agent *runtime / control-plane* design (not "agents" generically — there are 1,000 of those).
- Build a small, high-quality audience (~5–10k high-signal followers) of agent builders, model-co researchers, and infra-aware PMs.
- Generate a pull-not-push pipeline: 8–12 inbound recruiter / founder conversations per quarter by month 12.

**Non-goals (explicit).**
- Mass following / generic AI-influencer growth.
- "Prompt engineering" / "AI for productivity" lane — already saturated, dilutive to positioning.
- Course / paid-newsletter monetization. Audience is the asset; the role is the conversion event.

---

## 2. Positioning

### 2.1 The wedge

Most "AI thought leaders" in 2026 fall into three buckets:
1. **Researchers** — model internals, evals, alignment. High signal, narrow on product.
2. **VCs / pundits** — landscape maps, tweet threads. Wide reach, low builder credibility.
3. **PMs-who-prompt** — agentic workflows, GPT wrappers. High volume, low technical depth.

There is a fourth bucket, structurally underpopulated: **PMs who design and ship agent runtimes.** People who can hold an ARB review *and* a customer call. That bucket is what model companies and hyperscalers are actively hunting for, and it is the bucket Rahat already proves you sit in.

### 2.2 Positioning statement

> Venkat is a product leader who builds agent runtimes, not slides about them. He ships a sovereign, local-first multi-agent control plane (Rahat), writes about it the way an L8 architect would review it, and brings the cultural texture (Hyderabadi-Dakhini "Miya," personal-life forcing function) that keeps the work honest. If you're hiring the PM who will own the agent layer at a model company or hyperscaler, you want this person.

### 2.3 The three claims, each backed by an artifact

| Claim | Evidence (already exists) | Evidence (to produce) |
|---|---|---|
| **Architectural depth** — "thinks like an L8" | ARB-grade [ARCHITECTURE.md](./ARCHITECTURE.md) (11 sections, 6 ADRs, three-plane decomposition) | Talk: "What I learned building a sovereign agent runtime on a Mac Mini" |
| **Shipping discipline** — "ships, doesn't talk" | 330-case eval harness, three independent paths, 100% green; production launchd service; daily real use | Public eval dashboard; "The eval-gated agent" essay |
| **Genuine POV** — "has a thesis, not opinions" | Post-Chat thesis, local-first sovereignty, deterministic-core-LLM-at-edges, Charter-as-policy-chokepoint | Manifesto essay: "Agents are roles, not models"; talk version of same |

### 2.4 What you are NOT saying

- Not "I built a chatbot." (Miya is the surface; Rahat is the work.)
- Not "I prompt-engineered my way to fitness goals." (The math is deterministic; LLM is the *fallback*.)
- Not "I'm a thought leader." (You're a builder who happens to think clearly. The artifact does the talking.)

---

## 3. Target audience

### 3.1 Primary (the hiring decision-makers)

**Anthropic / OpenAI / DeepMind product leaders.** VPs of Product, Heads of Agents, founding PMs on agent platform teams. They live on Twitter/X, read carefully-written essays, hire from people who *show their work* over a 12-month horizon. They're allergic to prompt-influencer LinkedIn posts.

**Hyperscaler agent-platform leads.** GCP Vertex Agent Engine PM/eng directors, AWS Bedrock Agents leadership, Azure AI agent platform. They care about: control plane primitives, multi-agent state, governance, observability, evals. Rahat's three-plane architecture and Charter pattern speak directly to their roadmap.

**Founders of Tier-1 agent infra startups.** Sierra, Lindy, Cresta, Decagon, vertical agent winners. Hiring early product leadership; want PMs with real systems thinking, not just SaaS PM craft.

**Pain points / motivations:**
- Drowning in candidates who can spell "agentic" but can't reason about state, eval surfaces, or policy boundaries.
- Need PMs who can hold their own with research/eng leads (i.e., earn the room without being a former engineer).
- Care about cultural taste and shipping cadence over credentials.

**Where they spend time:** Twitter/X (researcher and infra circles), select Substacks (Stratechery, Latent Space, Every, Dan Shipper, Nathan Lambert), GitHub (yes, actually), AI Engineer Summit, NeurIPS/ICLR adjacent gatherings, podcast appearances by people they already trust.

### 3.2 Secondary (the amplification layer)

**Senior agent engineers and ML infra people** at Anthropic, OpenAI, GCP, Meta, MS, FAANG-adjacent. They're the ones whose retweet/quote signals to the primary audience that "this PM is real." Currency with them is *technical artifacts*, not opinions.

**Other agent-curious PMs and infra-aware founders.** They become advocates, repost essays, invite to Slack / Discord rooms, and create the warm-intro fabric that closes the loop on the primary objective.

**Indian-American / South Asian tech audience.** The "Miya" persona and Hyderabadi register are not a gimmick — they're a real differentiator and a community that amplifies hard. This audience converts to *speaking invitations and warm intros at Tier-1 firms*.

### 3.3 Audience-message fit summary

> Director-level decision-makers at model companies will read three artifacts: the architecture doc, one essay, and your last 30 days of public output. Optimize ruthlessly for those three surfaces.

---

## 4. Key messages

### 4.1 Core message (one line)

> **"Most agent discourse is about prompts. I build the runtime."**

### 4.2 Supporting messages and proof points

| Message | Proof point | Where it lives |
|---|---|---|
| Agents are roles, not models. The interesting work is the *runtime* that coordinates them. | Three-plane architecture; Miya-as-orchestrator; in-house ~1,400 LOC core/ rejected LangGraph/AutoGen/CrewAI for cause | ARCHITECTURE.md §4, ADR-1; manifesto essay |
| Deterministic core, LLM at the edges — health-adjacent agent work cannot be probabilistic. | Locked weight-loss math (0.75 lb/wk, 2,600 kcal); 142-case eval suite; LLM-fallback only with anti-hallucination guardrails | ADR-4; "eval-gated agent" essay |
| Local-first sovereignty is a product moat, not a privacy fig leaf. | Single Mac Mini, SQLite ledger, `cp` is a backup; Apple Watch passive ingestion; no cloud state | Sovereignty essay; talk |
| The Charter pattern: policy as code as the chokepoint that lets a 20-agent mesh stay coherent. | Python predicate registry; `governance_log`; quiet hours / HRV-red / external-veto; 0 coordination needed between agents | "Charter pattern" essay; conference talk |
| The eval suite is the spec. Every reported bug becomes a permanent test case. | 330 cases across 3 independent paths; production-bug-driven philosophy | "Eval-gated agent" essay |
| Cultural texture is design. Miya's Dakhini register is an architectural decision, not branding. | Voice layer in `core/voice.py`; deterministic phrasebook; eval-tested across 7 message kinds | "Miya as design choice" essay (latent-space submission) |

### 4.3 Tone / voice

- **Crisp, technical, low-ego.** No "exciting to share!", no rocket-ship emoji, no humblebrags.
- **Specific numbers and named decisions.** "330 eval cases," "ADR-3," "the Charter rejected a notify.recovery work-order at 22:47."
- **Show the trade-off you accepted.** Every claim paired with the cost.
- **Cultural register on personal channels (Miya, Dakhini phrasings) selectively.** It's a flavor, not the meal.

---

## 5. Channel strategy

### 5.1 Owned

| Channel | Purpose | Cadence | Effort |
|---|---|---|---|
| **GitHub** — `Rahat-Plane`, `modernthinker` | The canonical proof. Every other channel points here. | Continuous; weekly meaningful commits + release notes | High (already happening) |
| **Personal site** (modernthinker.build or similar) | Index of essays, talks, repo, "hire me" page. Fast, plain, no JS-heavy nonsense. | Static; updated per essay | Low after setup |
| **Substack / blog** (under `modernthinker`) | Long-form essays. The artifact channel. | 2/month months 1–6, then 1/month | High |
| **Twitter/X** (@modernthinker or similar) | Distribution + researcher/infra circle presence. | 4–6 substantive tweets/week, 1 thread/week (essay companion) | Medium |
| **LinkedIn** | Recruiter signal + South Asian network amplification. Repurposed essay chunks. | 1 long post/week, 2 short/week | Low (mostly repurposing) |

### 5.2 Earned

| Channel | Purpose | Cadence | Effort |
|---|---|---|---|
| **Podcast appearances** | Authority + warm intro fabric. Target: Latent Space, Cognitive Revolution, Practical AI, MLOps Community, Lenny's, Acquired (long shot), No Priors. | 1 per quarter year 1, 1 every 6 weeks year 2 | Medium |
| **Guest essays** | Borrowed audience. Target: Every (Dan Shipper), Latent Space (swyx), Stratechery (long shot), Interconnects (Nathan Lambert), Import AI (Jack Clark, long shot). | 1/quarter | Medium |
| **Conference talks** | The single highest-leverage credential signal. Target: AI Engineer Summit, AI Engineer World's Fair, Anthropic dev day, GCP Next, NeurIPS workshops (system papers track). | 2–3 in year 1, 4–5 in year 2 | High |
| **HN / Lobsters / r/LocalLLaMA** | Per-essay distribution. Don't post your own; line up someone else to. | Per essay | Low |

### 5.3 Community

- **AI Engineer Slack / Latent Space Discord / Anthropic Discord** — be present, be useful, never lead with your own work.
- **South Asian tech communities** — South Asian Founders, brown-tech Slack rooms — for amplification of the cultural-texture angle.
- **Office hours** — once month 6+: 30-min slots, 4 per month, free, advertised on the site. The single best inbound generator long-term.

### 5.4 Paid

**None planned.** The audience here is too small and too sophisticated for paid acquisition to convert. Money is better spent on a designer for the site, a video editor for talk recordings, and travel to one extra conference.

### 5.5 Channel selection rationale

The thesis is *artifact-led, not personality-led*. That dictates GitHub and long-form essays as the trunk, Twitter/X as the amplifier, and podcasts/talks as the trust-conversion layer. LinkedIn exists only because recruiters live there.

---

## 6. Content pillars

Every piece of content maps to exactly one pillar. If it doesn't, it doesn't ship.

### Pillar 1 — Runtime & control-plane design (40% of output)
The work itself. Architecture deep-dives, ADRs, design choices, tradeoffs.
**Anchors:** ARCHITECTURE.md, the three planes, Charter, decision tracing, episodic memory, Miya orchestrator.
**Audience:** primary (decision-makers), secondary (engineers).

### Pillar 2 — Agent PM craft (25%)
What it looks like when a PM owns the agent layer. Eval design, spec writing, working with research/eng, shipping cadence, knowing what's load-bearing vs. nice-to-have.
**Anchors:** the 330-case eval philosophy, "production-bug-driven" tests, Now/Next/Later promotion rule, "eval-gated changes."
**Audience:** primary (hiring managers gauging fit), other agent PMs.

### Pillar 3 — POV / theses (20%)
The arguments that make Venkat's voice distinctive. Each is opinionated and defended.
**Anchors:** Post-Chat era, local-first sovereignty, deterministic-core, "agents are roles not models," "the runtime is the moat."
**Audience:** all three.

### Pillar 4 — Building Rahat in public (10%)
The build log. New agent shipped, new ADR, new bug → new test. Honest, weekly, low ceremony.
**Anchors:** weekly thread on Twitter; monthly "what shipped in Rahat" Substack post.
**Audience:** secondary (creates trust through visible cadence).

### Pillar 5 — Cultural lens / Miya (5%)
Selective. Hyderabadi-Dakhini register, Indian-American family-life forcing function, why it shapes design.
**Anchors:** "Miya as design choice" essay; voice layer ADR-3.
**Audience:** South Asian amplification layer; differentiated voice signal for everyone else.

---

## 7. 12-phase roadmap (2 months per phase)

The structure mirrors Rahat's own Now/Next/Later — promote items only when trigger conditions land.

### Phase 1 (months 1–2) — Foundation. *"The artifacts exist."*
**Goal:** make sure the proof points are real, public, and discoverable.
- Polish [README.md](./README.md) and [ARCHITECTURE.md](./ARCHITECTURE.md). Already strong; tighten the top 30 lines.
- Stand up `modernthinker.build` (one page, links to repo, essay 1, hire-me).
- **Essay 1 (manifesto):** "Agents are roles, not models — what I learned building a sovereign agent runtime." Anchors the Post-Chat thesis. Ships to Substack, cross-posts as Twitter thread, submitted as guest piece to Latent Space.
- **Essay 2 (technical):** "The Charter pattern: policy as code in a multi-agent runtime." Pure architecture; for the engineer audience.
- Twitter/X: relaunch profile (handle, bio, pinned thread = manifesto). 4 substantive tweets/week, no fluff.
- LinkedIn: rewrite headline to reflect positioning; one essay-chunk post/week.

**Exit criteria for phase:** README + 2 essays live; site live; first 50 high-signal Twitter follows; 3 podcast outreach DMs sent.

### Phase 2 (months 3–4) — Velocity. *"It's clearly a cadence, not a moment."*
- **Essay 3:** "The eval-gated agent: 330 cases as the spec." Open-source the eval harness with redacted cases.
- **Essay 4:** "Local-first as the agent moat." The sovereignty thesis as a standalone piece.
- First podcast appearance ships (target: Latent Space or Cognitive Revolution).
- Apply for AI Engineer Summit talk (CFP usually 4–6 months out).
- Start "Build Log" weekly thread on Twitter — what shipped in Rahat this week. Honest, short, evidence-backed.

**Exit criteria:** First podcast aired; CFP submitted; ~750 followers; 1–2 inbound DMs from people at target companies.

### Phase 3 (months 5–6) — Authority signal. *"The right people are nodding."*
- **Essay 5:** "Decision tracing: how to debug a 20-agent mesh." Targeted at infra audience.
- **Essay 6:** "Now / Next / Later: a promotion rule for personal-AI roadmaps." Targeted at PM audience.
- Second podcast (Practical AI / MLOps Community).
- Conference talk delivered (AI Engineer or equivalent). Recording becomes evergreen artifact.
- Open office hours: 4 slots/month.

**Exit criteria:** Talk video live; ~1.5k followers; 4+ inbound recruiter conversations; first founder/VP-level DM.

### Phase 4 (months 7–8) — The Bajrangi shipment. *"The thesis is a track record."*
- Ship the **Bajrangi safety-veto agent** in Rahat. Write it up as ADR + essay: "Building the recovery-vs-performance dialectic in a multi-agent system." This is your second concrete agent — proves the runtime claim.
- **Essay 7:** "What 'agents' miss about state."
- Third podcast appearance.
- Apply for AI Engineer World's Fair / GCP Next / Anthropic dev day.

**Exit criteria:** 2 production agents in Rahat (Scientist + Bajrangi); 600+ eval cases; 2 conference talks scheduled.

### Phase 5 (months 9–10) — Field guide season. *"The reference work."*
- **The Field Guide series:** 3-part essay series — "A PM's field guide to agent architecture." Becomes the most-cited piece of the campaign. Treated as a small-book deliverable, not a blog post.
- One guest essay published in Every or Interconnects.
- Speaking at second conference.
- Open: paid advisory inquiries. Take 1–2 only — credibility, not income.

**Exit criteria:** Field Guide cited in at least 2 third-party places (newsletter mentions, panel references); ~3k followers; 8–10 inbound conversations/quarter.

### Phase 6 (months 11–12) — Inflection. *"The hire conversations begin."*
- **Essay 10 (capstone):** "Year one of building Rahat: a public retrospective." Numbers, decisions, mistakes, ADRs reversed.
- Coach (Fraser) and Curriculum agents shipped — three-agent mesh.
- Selective interviewing begins. Target: 2 model companies, 1 hyperscaler, 1 startup, in deliberate order. Not optimizing for offer count; optimizing for *learning what each role would be*.

**Exit criteria:** Active conversations with at least 3 of the 4 target archetypes; outline of a "what role would be ideal" private memo.

### Phases 7–12 (months 13–24) — Land.

**Themes by quarter:**

- **Q5 (months 13–15):** Convert visibility to opportunity. Less new content (1 essay/month), more 1:1 conversation. Refresh the case study with fourth and fifth agents shipping. **Bajrangi-driven recovery study** as a single, deeply-evidenced essay.
- **Q6 (months 16–18):** Strategic interviews. Decline noise. Be willing to walk. The campaign's whole purpose is *power to choose*, not "any offer."
- **Q7 (months 19–21):** Decision and onboarding. Continue the build log even after signing — the work doesn't stop being yours.
- **Q8 (months 22–24):** Compound. Talks at the new company's events, recruit 1–2 people from your audience to your team, write the "year one" reflection from inside the new role.

**Promotion rule for the back half:** Don't add a new content surface (newsletter, podcast) unless the trigger condition is "≥3 invitations to do exactly that." Build until pulled.

---

## 8. Content calendar — months 1–6 (week by week)

| Wk | Pillar | Piece | Channel | Notes / dependencies |
|---|---|---|---|---|
| 1 | 4 | Build-log thread #1: "Why I built Rahat" | Twitter/X | Companion to README — points readers there |
| 1 | 1 | README polish + tighten ARCHITECTURE intro | GitHub | Must precede essay 1 |
| 2 | 3 | **Essay 1: "Agents are roles, not models"** (manifesto) | Substack + LI + X thread | Pin on profile |
| 2 | — | Site live: modernthinker.build | Owned | Static, single page |
| 3 | 4 | Build-log thread #2 | X | "What shipped in Rahat this week" |
| 3 | — | Latent Space / Cognitive Revolution outreach DMs | Email/X | Pitch essay 1 as guest piece + podcast topic |
| 4 | 1 | **Essay 2: "The Charter pattern"** | Substack + HN submit (via friend) | Heaviest engineering piece — let it rip |
| 4 | 4 | Weekly build-log | X | |
| 5 | 5 | LinkedIn long post: "Why I named my orchestrator Miya" | LinkedIn | Cultural-lens piece, repurposable |
| 5 | 4 | Build-log thread | X | |
| 6 | 2 | **Essay 3: "The eval-gated agent"** | Substack + X thread | Open-source eval harness w/ redacted cases |
| 6 | — | First podcast recorded | External | |
| 7 | 4 | Build-log thread | X | |
| 7 | — | Apply: AI Engineer Summit CFP | External | Talk: "A sovereign agent runtime" |
| 8 | 3 | **Essay 4: "Local-first is the agent moat"** | Substack + X thread | Sovereignty thesis standalone |
| 8 | — | First podcast airs | External | Promote relentlessly |
| 9 | 4 | Build-log thread | X | |
| 9 | 1 | Twitter thread: "Three planes — what I'd say at an ARB" | X | Companion deep-dive |
| 10 | 1 | **Essay 5: "Decision tracing for agent meshes"** | Substack | Infra-audience targeted |
| 10 | 4 | Build-log thread | X | |
| 11 | 2 | **Essay 6: "Now / Next / Later"** | Substack + LI | PM-audience targeted |
| 11 | — | Second podcast recorded | External | |
| 12 | 4 | Build-log thread | X | |
| 12 | — | Conference talk delivered (if accepted) | External | The big artifact for the half |
| 13–24 | mix | Continue 1 essay every 2 weeks; weekly build-log; 1 podcast/month; 2nd CFP submission | All | |

**Dependencies and gates:**
- Essay 1 cannot ship until README + ARCHITECTURE polish lands. **Both already exist** — only edits needed.
- Essay 3 (eval-gated agent) requires open-sourcing the harness. Block out a weekend for this in week 5.
- Conference talk requires a 4–6 month CFP lead. Submit by week 7 latest.
- Podcast appearances require warm-list outreach by week 3.

---

## 9. Content pieces inventory

**Already exists (use as-is or with tightening):**
- ARCHITECTURE.md (ARB-grade, 11 sections)
- README.md (16k chars, well-structured)
- PRD: The Rahat Plane
- 330-case eval suite (artifact)
- Public commit history showing real shipping cadence

**Must-have, year 1:**
1. Manifesto essay — "Agents are roles, not models"
2. Charter-pattern essay
3. Eval-gated-agent essay + open-source eval harness
4. Local-first sovereignty essay
5. Decision-tracing essay
6. Now/Next/Later essay
7. Bajrangi build-up essay (proof point #2)
8. Field Guide series (3 parts)
9. Year-one capstone essay
10. Two conference talks (recorded, with slides published)
11. Five podcast appearances (with show notes that link back)
12. modernthinker.build site (one page, no JS framework)

**Nice-to-have:**
- Mini-doc on the Mac Mini setup (developer audience candy)
- Public talk-track / slide template open-sourced
- A "How I'd hire an agent PM" essay (subtle: signals what you'd want to be hired as)

---

## 10. Success metrics

### 10.1 Primary KPI
**Inbound conversations from target companies/founders, per quarter.**
- Baseline (today): 0–1
- Q1: 2–3
- Q2: 4–6
- Q3 (month 9): 8–12
- Q4 (month 12): 10–15 sustained
- Year 2: opportunity to choose, not the count.

### 10.2 Secondary KPIs

| KPI | Q1 | Q4 | Q8 | Tracking |
|---|---|---|---|---|
| Twitter/X followers (high-signal) | 250 | 1.5k | 5–8k | Manual quarterly review of who follows |
| GitHub stars on Rahat-Plane | 50 | 500 | 2k+ | GitHub |
| Substack subscribers | 100 | 1k | 4k | Substack |
| Podcast appearances | 1 | 4 | 12+ | Tracker doc |
| Conference talks delivered | 0 | 1 | 4–5 | Tracker doc |
| Inbound recruiter contacts | 1–2 | 5–8/Q | 12+/Q | Notion/Airtable |
| Founder/VP-level inbound DMs | 0 | 2–3 | 6–10/Q | Same |
| Citations of Rahat in third-party content | 0 | 3 | 15+ | Manual + Google Alerts |

### 10.3 Qualitative checks (every 90 days)

- Did at least one essay get *forwarded internally* at a target company? (Ask warm contacts directly.)
- Are senior researchers / infra leads (not just PMs) engaging publicly?
- Has someone outside your network described you in writing as "the runtime guy" (or equivalent)?
- Is the technical bar of your output going up or flat? (Output regress = positioning regress.)

### 10.4 The single anti-metric

**Do not optimize for:** total impressions, viral threads, "creator" engagement-bait. A 50k-impression hot-take thread is *negative signal* to the primary audience. Walk away from it.

---

## 11. Budget

Channel-agnostic; the campaign trades time for outcome, not money. Approximate annual spend:

| Item | Year-1 cost | Why |
|---|---|---|
| Designer for one-page site | $500–1,500 | Looks matter for the hire-me page |
| Domain + hosting | ~$50/yr | modernthinker.build |
| Substack | $0 | Free tier sufficient |
| Travel to 2 conferences | $2k–4k | AI Engineer + 1 other |
| Talk recording / editor | $500/talk | Crisp YouTube versions of conference talks |
| Optional Apple Watch / hardware for content | $0 | Already owned |
| **Year 1 total** | **~$4k–7k** | All-in |

If budget were unlimited, the only meaningful additions would be: a part-time editor for essays ($200/essay), and travel to 2 more conferences. **None of that changes the outcome materially.** The cost of this campaign is your weekends and weekday-evening writing time, not money.

---

## 12. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Voice dilution.** Tweeting takes effort that pulls from essay/build time. | High | Hard rule: essays > tweets. Skip a week of tweets if essay slips. |
| **Hot-take temptation.** Engagement-bait threads will out-perform technical posts in raw numbers. | High | Track only primary KPI. Mute notifications. Treat numbers as poison if they come from the wrong source. |
| **Rahat stalls.** Build-log credibility collapses if real shipping pauses for >4 weeks. | Medium | Bajrangi by month 7 is the forcing function. If it slips, the whole calendar slips by the same amount — don't compensate with more posts. |
| **Hireability paradox.** A loud public profile can scare conservative hiring managers. | Medium | Mitigated by *artifact-led* approach. The repo and architecture doc do the talking; you're not visibly chasing the role. |
| **Cultural angle misread.** Miya/Dakhini lens could be misread as gimmick. | Low–Medium | Keep it ≤5% of output, always tied to a design choice. Never lead with it for technical audiences. |
| **Burnout.** 24-month plan + day job + toddler + newborn. | High | Quarterly checkpoint (90-day review section): pause, rescope, don't push through. The plan must survive a hard quarter without breaking. |
| **Shifting agent market.** "Agent" hype could flip to backlash by year 2. | Medium | The runtime / control-plane framing is *less* hype-coupled than "agent" generically. If the word goes out of fashion, "agent runtime" still doesn't. |
| **NDA / employer constraints (Google).** Some content angles may be career-sensitive. | Medium | All output is about Rahat (personal project, weekend code). Never write about employer-internal product details. Run anything ambiguous past your employer's policy team. |

---

## 13. Genuineness rules (non-negotiable)

The campaign is anchored to the Rahat repo and the modernthinker GitHub identity. The genuineness rules below are what keep the campaign from drifting into "personal brand" territory.

1. **Every claim links to code.** If an essay describes the Charter, the essay must link to `core/charter.py`. No abstract architecture without a file path.
2. **Every essay names a tradeoff.** If you can't articulate what you gave up, you haven't thought about it hard enough.
3. **Build-log is honest.** When something doesn't ship, say "didn't ship; here's why." Skipped weeks are documented, not hidden.
4. **No ghostwriting.** All essays are first-person, written by Venkat. An editor for grammar is fine; an editor for ideas is not.
5. **No agent-AI-generated essays masquerading as your writing.** Use the model to think, not to write the final draft. The audience can tell, and the cost of being caught is the entire campaign.
6. **Voice on personal channels (Miya register) is selective.** It's part of the texture; it never replaces clarity.
7. **The repo stays alive.** No public claims of "I built X" if the commit history says otherwise.
8. **References to the day job stay generic.** "Bay Area PM" / "PM at a large tech company" is enough. The campaign is about Rahat and Venkat, not the employer.

---

## 14. Next steps (this week)

1. **Tighten README.md** — top 30 lines. Specifically: lead with what Rahat *is* in one sentence, before the "why this exists." [1 hour]
2. **Tighten ARCHITECTURE.md** — formatting fixes (escaped underscores, table render); section 1 executive summary tightened to 5 bullets max. [2 hours]
3. **Register `modernthinker.build`** (or chosen domain). [15 min]
4. **Outline Essay 1: "Agents are roles, not models."** Three-bullet outline + opening paragraph. [1 hour]
5. **Twitter/X profile rewrite:** handle, bio ("PM building Rahat — a sovereign agent runtime. modernthinker.build"), pin a thread that links to README. [30 min]
6. **Make a tracker** — single Notion or Airtable doc with: pipeline (target companies, status, notes), content calendar, podcast outreach list, KPI dashboard. [1 hour]
7. **Pick the four podcast targets** for first outreach: Latent Space (swyx), Cognitive Revolution, Practical AI, MLOps Community. Draft warm DM. [30 min]

**Total this-week investment: ~6 hours.** All on weekend.

---

## 15. The single rule that makes this work

> **Build the work. Show the work. Let the work pull the role to you.**

The campaign succeeds when, 18 months from now, the conversation a hiring VP at a model company has with a colleague is:
> "We should talk to that PM who built Rahat — the local-first agent runtime. Have you read his architecture doc?"

That sentence is the success metric. Everything in this plan exists to make it inevitable.

— end of campaign brief —
