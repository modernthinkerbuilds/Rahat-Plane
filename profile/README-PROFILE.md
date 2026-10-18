# Venkat Sadras

> **Bay Area PM at a large tech company. Miya is my personal AI agent. Rahat is the platform underneath it: checks in the path, a harness every answer runs through, and a record of every decision. It runs in my life every day.**

<a href="https://modernthinkerbuilds.github.io/Rahat-Plane/specs/RAHAT-ARCHITECTURE.html"><img src="https://raw.githubusercontent.com/modernthinkerbuilds/Rahat-Plane/main/specs/img/rahat-map.gif" width="100%" alt="Rahat end to end: a /week message travels from Telegram through Miya's runner and the routing ladder to Kobe, with the checks, the control plane and the vault around it"></a>

<sub>A <code>/week</code> message moving through Rahat. Click it for the live page, then tap any box.</sub>

| [**See it move →**](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/RAHAT-ARCHITECTURE.html) | [**Inside one agent →**](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/ADR-018.html) | [**The code →**](https://github.com/modernthinkerbuilds/Rahat-Plane) |
|:---:|:---:|:---:|
| The whole system on one page. Tap any box, or watch a message travel. | The harness every answer runs through, step by step. | Open source (Apache 2.0): the architecture, the decision records, the tests. |

**Miya** is the one I talk to. Behind it, Rahat runs six agents: Miya orchestrates Kobe (the numbers), Fraser (workout design), Huberman (the 9:30 pm cooldown) and Genie (weekends, the household calendar, local events). Bourdain answers "where do we eat?" on its own door while it's being tested.

An agent can be fluent and wrong at the same time, and you can't tell from reading it. Run more than one and they contradict each other, act at moments they shouldn't, and remember the wrong things.

Those problems don't live in the model. They live in the layer underneath it. That layer is what I build.

---

## What's running today

On a Mac mini at home, around the clock. Every line is in the repo.

- **Checks in the path, not requests in the prompt.** A validator checks Miya's replies against what's actually true, with seven checks, and rewrites a reply before it sends. Fraser's workout designs pass a 10-point contract in code before they go out: one repair if something fails, and if it still fails the reply says what isn't verified.
- **A harness for agents** ([ADR-018](https://github.com/modernthinkerbuilds/Rahat-Plane/blob/main/specs/ADR-018-outcome-contracts-and-independent-evaluation.md)). Eight steps from understanding the ask to recording the run, with the outcomes each answer must meet checked in code. One agent is on it (Bourdain), and a second uses its contract (Fraser). [Watch an ask go through it →](https://modernthinkerbuilds.github.io/Rahat-Plane/specs/ADR-018.html)
- **A judge that has to earn its vote.** A second model grades what code can't, like taste and fit. It stays in shadow until it agrees with my own labels: 80% on at least 50 of them before it may annotate, 90% on one outcome before it may block. Today it has 3 labels, so it's all shadow.
- **Knowledge with receipts.** Every fact in Bourdain's knowledge base carries its source and the date it was read: 326 places and 3,397 facts as of Oct 14.
- **Evals that replay for $0.** Every model and tool call is recorded against its request, so a run replays without a network or a bill. The golden sets are 72 real asks, kept private; the public tests run on synthetic twins.
- **A decision ledger.** One row per decision, with the agent, the rule, the latency and the cost: 7,408 rows since May 8.
- **It tells on itself.** One line at 08:15 when something is broken (a dead feed, a stale page, a silent Watch, lost messages, refused model calls), and silence otherwise.
- **Real data in.** 121 local event sources, 43 of them read in plain code with no model; 150+ health metrics at minute resolution.
- **4,209 tests**, behind a pre-push gate that blocks anything red, including a push that would publish a private word.

**[→ How Rahat actually runs](https://github.com/modernthinkerbuilds/Rahat-Plane/blob/main/specs/ARCHITECTURE.md)**

---

## How an agent joins

I don't add an agent straight into the assistant I talk to. Each one starts on its own front door, with its own bot and its own tests, where its failures are easy to see and can't break anything else. When it passes, it's wired in behind Miya, and Miya stays the one place I ask.

Genie came in that way: it started on its own bot, and now it also answers through Miya, keeping its own bot for the household. Bourdain is next. It's still on its own door, running on the harness and being tested, and it joins Miya when it passes.

Orchestration is the part I'm least done with. Today Miya runs a 16-rung ladder of rules in code: when a rule recognizes the ask (a command, a log, a design request, a cooldown, a lookup), that agent answers without a model; anything no rule claims goes to Miya's model, which draws on the specialists and answers in one voice. When two specialists disagree, it reconciles them, best-effort. Where I'm taking it: each agent declares what it does in a manifest (Bourdain already has one, for the harness), and a model reads every ask and hands it to the right agent, with the code rules still in front for the asks I already know. That routing isn't built yet.

---

## The rewrite that mattered

A few weeks in, Miya told me, cheerfully and in a flawless voice, a fact about me that was simply false, stated with total confidence.

My first instinct was the wrong one: I went to the prompt. Ten patches later I was treating a systemic failure as a run of bad luck. So I asked the question I should have asked first: not *why did the model get it wrong*, but *which layer of my system was supposed to catch this?*

Nothing was there.

So I stopped adding features and rebuilt the core: what's actually true about me goes in front of the model, a validator sits between the agent and me, and every specialist's output is re-voiced into one voice.

The design decision that mattered: **the check runs on the way out, not as a request inside the prompt.** A rule in a prompt is a request. A check in the path is a guarantee.

I tested it by replaying **126 real messages** from my own history. Internal-voice leaks went to **0%**, and the validator caught and corrected contradictions in about **5%** of replies. The failure was never rare. It was invisible.

---

## The claim, and two tests of it

The argument for building the operating system instead of the bot is that the next agent should be configuration, not a rebuild.

**Genie was the first test.** Same memory, same rulebook, same decision log, and a new job on top: weekends, a household calendar that schedules rather than lists, and a digest of local events. My wife uses it too, from her own phone. **The next agent wasn't a rebuild. It was a weekend.**

**Bourdain is the harder one.** It went live on its own bot the night before a family trip, ran the trip, and on Oct 4 became the first agent to answer through the shared harness. In its first week there it made 22 harness runs between itself and Fraser: one household's volume, not a benchmark. A Bourdain answer takes about a minute.

The direction I'm building toward: the database answers first; code applies the facts (who is eating, how far, open now); the model applies taste, once; the internet is touched only when the database comes up short. That path isn't built yet.

---

## The bet I'm actually making

The system is built toward three things it doesn't fully do yet. I name them as the bet, not the brochure: the fastest way to lose trust here is to claim the vision as shipped.

- **Outcomes, not answers.** *Measuring shipped for one agent; learning not built.* Each Bourdain answer is checked against named outcomes and recorded, and the golden set replays for $0. A loop that changes behavior from outcomes, and proves a cheaper model kept the result, is not built.
- **Memory that keeps what *mattered*.** *The bet.* Shared typed memory is table stakes now (Letta, Mem0). Recall conditioned on what changed an outcome, not on what was recent, is not built.
- **A receipt for every fact.** *Partial, one agent.* Bourdain's facts carry their source, method, date and freshness, and a misread can be forgotten. The rest of the system's knowledge doesn't carry receipts yet.

---

## Honest scope

Rahat is personal-scale, not enterprise software. My data lives on a Mac mini at home; each model call sends that request's context to the model provider, and messages travel through Telegram. But a fluent agent that's confidently wrong, agents that disagree, a decision you can't prove, an evaluator you can't trust: those are exactly the problems a company hits the day it runs a fleet. Solving them in a house, where I feel every failure the same day, is an honest way to learn them.

---

## About me

A Bay Area PM at a large tech company, hands-on with multi-agent systems. I care about building the kind of agents I'd actually point at my own life: ones that remember, coordinate, check themselves, and know when *not* to act.

**The architecture, the decision records and the tests are public: [github.com/modernthinkerbuilds/Rahat-Plane](https://github.com/modernthinkerbuilds/Rahat-Plane).** Happy to walk through any of it.

<sub>Views my own, not my employer's.</sub>
