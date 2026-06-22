"""Eval layer — scenario fidelity tests for the Sports Scientist + Miya.

The unit/contract layers (tests/test_*.py) prove the *plumbing* works.
The eval layer proves the *behavior* is right against the lived-in
scenarios from the months-long Gemini coaching thread (PDF reference:
`Sports Scietist with gemini.pdf`).

Each eval case has two assertions where possible:

  * **Deterministic** — substring / numeric / structural check that does
    NOT depend on an LLM. This is the regression bar.
  * **LLM-as-judge (optional)** — when GEMINI_API_KEY is configured, a
    rubric grade is run via Gemini Flash. Skipped silently otherwise so
    CI stays offline.

Two design rules keep this layer healthy:

  1. **Hermetic.** `RAHAT_TEST_MODE=1` is enforced in conftest, the
     google.genai stub is loaded before any rahat import, and a fixture
     plan/DB is materialized per test. No live `vault/rahat.db`. No
     network. The 2026-05-08 corruption incident exists to stop us from
     ever weakening this.

  2. **Numbers preserve.** The voice layer adds wrapping, never alters
     data. Every eval that prints a calorie / weight / HR number asserts
     the digits survive verbatim.
"""
