"""Deep end-to-end mesh flows through the REAL stack (2026-05-23).

Most of the Day-11–15 tests are component-level (composer alone, dispatcher
alone, handler alone). This file exercises the FULL path a Telegram message
actually travels:

    miya.route(msg, chat_id)            # orchestrator
      → Tier-1 slash bypass  → Kobe     # /pain, /profile, /pace
      → Tier-2 classifier    → agent    # natural language
        → agent.route(chat_id, db_path) # _safe_route capability negotiation
          → Kobe dispatcher  → handlers # plan edits, WOD lookups, persistence
          → Fraser composer  → LLM      # design + conversational follow-ups

Only two things are mocked: the classifier (miya.classify_intent → fixed
scores, so routing is deterministic) and the LLM (core.io.llm_generate →
scripted composer output). Everything else — agent dispatch, chat_id
threading, substrate reads/writes — is the real code. State persists to the
hermetic temp DB (cio.DB_PATH, set by the registry conftest).

If any link in the chain regresses (chat_id dropped, slash misrouted, a plan
edit not persisting, a WOD lookup re-designed by Fraser) one of these fails.
"""
from __future__ import annotations

import pytest

from core import miya
from core import io as cio
from agents.fraser.agent import FraserAgent
from agents.the_scientist.agent import KobeAgent


# ─────────────────────────── Mesh harness ───────────────────────────
_SESSION_4SECTION = (
    "## Part 1: Warm-up (10 min)\n- cat-cow, face pulls (*chest up*)\n"
    "## Part 2: Strength (20 min)\n"
    "- Back Squat 60 kg (132 lbs) — 60% of 102 kg max\n"
    "## Part 3: WOD / Metcon (18 min)\n- row + KB swings (~420 kcal)\n"
    "## Part 4: Cool-down (10 min)\n- legs up the wall, box breathing\n"
    "### Coach's Note\nExhale on exertion; shoulders back.")

_FOLLOWUP_ANSWER = (
    "Your Back Squat today is **60 kg (132 lbs)** — 60% of your 102 kg "
    "max. Heels on 2.5 lb plates, exhale on the drive up.")


@pytest.fixture
def mesh(monkeypatch):
    """Register Kobe + Fraser, mock the classifier and the LLM. Returns a
    handle exposing .route(), the captured LLM prompts, and a setter for
    routing scores."""
    miya.register(KobeAgent())
    miya.register(FraserAgent())

    state = {"scores": {"kobe": 0.5, "fraser": 0.5}}
    prompts: list[str] = []

    monkeypatch.setattr(
        miya, "classify_intent",
        lambda msg, db_path=None: dict(state["scores"]))

    def _llm(prompt, *a, **k):
        prompts.append(prompt)
        if "OUTPUT (FOLLOW-UP ANSWER)" in prompt:
            return _FOLLOWUP_ANSWER
        return _SESSION_4SECTION

    monkeypatch.setattr(cio, "llm_generate", _llm)

    class _Mesh:
        def to(self, **scores):
            state["scores"] = scores
        def route(self, msg, chat_id=None):
            return miya.route(msg, chat_id=chat_id)
    m = _Mesh()
    m.prompts = prompts
    return m


def _is_4section(text: str) -> bool:
    low = (text or "").lower()
    return all(p in low for p in ("part 1", "part 2", "part 3", "part 4"))


# ─────────────────────── 1. Design + follow-up ──────────────────────
class TestDesignAndFollowupFlow:
    def test_design_request_reaches_composer(self, bootstrap_substrate, mesh):
        mesh.to(fraser=0.95, kobe=0.05)
        reply = mesh.route("design me a 60 minute session for today",
                           chat_id="C-DESIGN")
        assert reply is not None and _is_4section(reply.text)

    def test_followup_answers_against_prior_session(self, bootstrap_substrate,
                                                     mesh):
        """The multiplier: chat_id must thread miya.route → Fraser →
        composer → chat_memory so the follow-up resolves against the
        session, not regenerate a new one. End-to-end proof."""
        mesh.to(fraser=0.95, kobe=0.05)
        mesh.route("design me a session for today", chat_id="C-FU")
        reply = mesh.route("what weights should I follow?", chat_id="C-FU")
        assert reply is not None
        assert not _is_4section(reply.text), (
            "a follow-up must be ANSWERED, not regenerated as a new "
            "4-section session — proves the conversational mode is live "
            "through the whole mesh")
        assert "60 kg" in reply.text
        assert "OUTPUT (FOLLOW-UP ANSWER)" in mesh.prompts[-1]

    def test_followup_is_isolated_per_chat_id(self, bootstrap_substrate, mesh):
        """A first message in a DIFFERENT chat has no history, so it must
        be a fresh design — not treated as a follow-up to another chat."""
        mesh.to(fraser=0.95, kobe=0.05)
        mesh.route("design me a session for today", chat_id="C-A")
        reply = mesh.route("what weights should I follow?", chat_id="C-B")
        assert _is_4section(reply.text), (
            "chat C-B has no prior session; must design, not borrow C-A's")


# ─────────────────────── 2. /pain → adaptation ──────────────────────
class TestPainFlowFeedsComposer:
    def test_slash_pain_persists_and_reaches_design_prompt(
            self, bootstrap_substrate, mesh):
        from core import pain_state
        # Report pain via the slash command (Tier-1 → Kobe).
        reply = mesh.route("/pain left shoulder sharp")
        assert reply is not None and "left shoulder" in reply.text
        assert pain_state.has_pain_at("shoulder"), "pain must persist"

        # Now a design request — the composer's prompt must carry the pain.
        mesh.to(fraser=0.95, kobe=0.05)
        mesh.route("design me a session for today", chat_id="C-PAIN")
        design_prompt = mesh.prompts[-1]
        assert "left shoulder" in design_prompt.lower(), (
            "active pain must be injected into the design prompt so Fraser "
            "adapts the session around it")


# ─────────────────────── 3. /profile persistence ────────────────────
class TestProfileFlow:
    def test_slash_profile_set_persists(self, bootstrap_substrate, mesh):
        from core import athlete_profile
        reply = mesh.route("/profile set deadlift 165")
        assert reply is not None and "165" in reply.text
        assert athlete_profile.get(refresh=True).one_rms["deadlift"] == 165.0


# ─────────────────────── 4. Plan edits persist ──────────────────────
class TestPlanEditFlow:
    def test_pick_persists_through_mesh(self, bootstrap_substrate, mesh):
        from agents.the_scientist import state as st
        monday, _ = st.week_bounds()
        st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None)
        mesh.to(kobe=0.95, fraser=0.05)   # plan edit is Kobe territory
        reply = mesh.route("pick Sun for crossfit")
        assert reply is not None
        assert 6 in st.get_prefs(monday)["forced_cf_days"], (
            "an additive pick must persist through miya → Kobe → dispatcher")

    def test_rest_day_persists_through_mesh(self, bootstrap_substrate, mesh):
        from agents.the_scientist import state as st
        monday, _ = st.week_bounds()
        st.set_prefs(monday, forced_cf_days=[0, 2, 4], forced_z2_day=None,
                     unavailable_days=[])
        mesh.to(kobe=0.95, fraser=0.05)
        mesh.route("Wed rest")
        assert 2 in st.get_prefs(monday)["unavailable_days"]


# ─────────────────────── 5. WOD lookup ≠ design ─────────────────────
class TestWodLookupNotDesigned:
    def test_wod_for_named_day_routes_to_gym_lookup_not_composer(
            self, bootstrap_substrate, mesh):
        """'what is the WOD for Tuesday' classified to Kobe must hit the
        gym lookup (handle_gym_wod_on) — NOT fall through to Fraser's
        composer. (A NAMED weekday is a schedule peek; 'today' is
        intentionally Fraser's daily-driver design intent, tested
        elsewhere.) We assert the composer LLM was never called."""
        mesh.to(kobe=0.95, fraser=0.05)
        reply = mesh.route("what is the WOD for Tuesday")
        assert reply is not None
        assert not _is_4section(reply.text), "must be a lookup, not a session"
        assert mesh.prompts == [], (
            "the composer LLM must not run for a gym-WOD lookup")


# ─────────── 5b. Fraser delegates day-specified lookups to Kobe ─────
class TestFraserDelegatesLookups:
    def test_lookup_misrouted_to_fraser_delegates_to_kobe(
            self, bootstrap_substrate, mesh):
        """Runtime backstop for the 2026-05-17 misroute: even if the
        classifier hands Fraser a day-specified WOD lookup, Fraser
        delegates to Kobe instead of designing. The composer LLM must
        never run."""
        mesh.to(fraser=0.95, kobe=0.05)   # deliberately misroute to Fraser
        reply = mesh.route("what is my workout for Tuesday")
        assert reply is not None
        assert not _is_4section(reply.text), (
            "a lookup must not be re-designed by Fraser")
        assert mesh.prompts == [], (
            "the composer must not run for a delegated lookup")

    def test_design_naming_a_day_stays_with_fraser(self, bootstrap_substrate,
                                                    mesh):
        """A DESIGN request that happens to name a day must NOT be
        delegated — Fraser designs it (the design verb wins)."""
        mesh.to(fraser=0.95, kobe=0.05)
        reply = mesh.route("design me a session for Friday", chat_id="C-DZ")
        assert _is_4section(reply.text)


# ─────────────────────── 6. chat_id end-to-end ──────────────────────
class TestChatIdThreadsEndToEnd:
    def test_chat_id_reaches_chat_memory(self, bootstrap_substrate, mesh):
        """After a design turn, chat_memory for that chat_id must hold the
        turn pair — the proof chat_id survived the whole call chain."""
        from core import chat_memory
        mesh.to(fraser=0.95, kobe=0.05)
        mesh.route("design me a session for today", chat_id="C-MEM")
        turns = chat_memory.recent("C-MEM", n=4)
        assert len(turns) >= 2, (
            "the (user, bot) turn pair must be recorded under the chat_id "
            "that miya.route received")
        assert any(t.role == chat_memory.ROLE_USER for t in turns)
        assert any(t.role == chat_memory.ROLE_BOT for t in turns)
