# Venkat Sadras

> **Bay Area PM at a large tech company. I build and run Rahat — an operating system for a household of personal AI agents: one shared memory, one rulebook, one orchestrator, and a record of every decision it makes. Live in my own life, daily, for months.**

My bet, in one line: AI gets useful when the environment around the model remembers what *mattered*, coordinates across agents, and is measured against whether my life actually got better — not only when the model gets smarter. In platform terms, that environment is a *control plane* for agents, built at human scale.

---

## Read the machine, not the subject

The examples below happen to involve training and recovery. **Ignore the subject — watch the machine.** I run Rahat against my own life because it's the most unforgiving test I have: a domain where I notice a wrong answer the same day. The agents on top coach training today; Rahat is the operating system underneath, and it doesn't care what they're about. The tell is that the hard problems it solves have nothing to do with the subject — they're the problems *anyone* running a fleet of agents hits.

**One quick map before the scenes.** I talk to a single assistant — **Miya**, the orchestrator. Behind him sit specialists, each named for someone whose life embodies its job (Genie plans the family weekend; others handle dinner, gifts, training). The **Charter** is the one rulebook every action clears before it runs. That's the whole cast below.

---

## The rewrite that mattered

A few weeks in, Miya told me — cheerfully, in a flawless voice — a fact about me that was simply false: a number from my own profile it had never been given, stated with total confidence.

That is the real problem with agents right now. Not that they can't talk — that they're **fluent and wrong at the same time, and you can't tell.**

So I stopped adding features and rewrote the core. Now every reply is grounded against what's actually true about me and fact-checked — and rewritten — *before* it sends. Every specialist's output is re-voiced into one voice instead of leaking its own. Every decision is logged with its reasons. I tested the rewrite by replaying 126 real messages from my own history. The invented facts got caught and corrected; the internal-voice leaks went to zero.

None of that is a feature of any one domain. It's what makes an agent trustworthy — and it's the thing most multi-agent demos quietly skip.

---

## Two more things a chatbot can't do

**Resolve a conflict instead of handing me two.** Fraser, the specialist that designs my sessions, has a hard one queued for tomorrow. Kobe, the one watching recovery, sees my numbers in the red and says hold. Two specialists, opposite calls. A switchboard forwards both and lets me referee. Miya weighs them and comes back with *one* answer — *not tomorrow; here's the lighter version* — in a single voice. That's the line between routing and orchestration, and it's the thing a switchboard structurally can't do. *(Today it's best-effort; making it airtight is part of the build.)*

**Tell me why it did what it did.** Ask Rahat to act at the wrong moment and the Charter — the single rulebook every action passes through — stops it before any agent moves, and logs why. Every decision lands as one row: which agent, which rule, how long, what it cost. When an answer surprises me, I replay it instead of guessing. It all runs on a Mac Mini at home — my data never leaves the machine — and the model underneath is swappable without moving any of it.

---

## Where it goes — same machine, new face

Swap the question to *"what should we do this weekend?"* and the same machinery runs under **Genie**: he checks the baby skipped his nap, that Saturday's the open day, that last weekend's crowded museum was a miss — and hands back one plan, not three browser tabs. Same memory, same rulebook, same decision log; a different lens on top. That's the whole point of building the operating system and not the bot: the next agent is a prompt and a tool list, not a rebuild.

---

## The bet I'm actually making

None of the above is the finish line. The system is built toward three things it doesn't do yet — and they're the reason it exists. I name them as the bet, not the brochure, because the fastest way to lose trust here is to claim the vision as shipped.

- **Outcomes, not answers.** A loop that closes on whether my life got *better* — and proves a cheaper model didn't cost me the result.
- **Memory that keeps what *mattered*.** Shared, typed memory is table stakes now (Letta, Mem0). The bet is recall conditioned on what actually changed an outcome, not on what was recent.
- **A receipt for every fact.** Each thing the system knows tagged with where it came from, how fresh it is, and my right to delete it.

---

## Honest scope

Rahat is personal-scale, not enterprise software. But a fluent agent that's confidently wrong, agents that disagree, a decision you can't prove, memory that keeps the wrong things — those are exactly the problems a company hits the day it runs a fleet. Solving them in a house, where I feel every failure the same day, is an honest way to learn them.

---

## About me

A Bay Area PM at a large tech company, hands-on with multi-agent systems. Hyderabad roots. Husband, and father of two — including a newborn who supervised most of the early build during parental leave. I care about building the kind of agents I'd actually point at my own life: ones that remember, coordinate, and know when *not* to act.

The repo is private while I scrub personal data out of its history — happy to walk through the architecture on request.

<sub>Views my own, not my employer's. Drafted with Claude, edited and verified by me.</sub>
