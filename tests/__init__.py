"""Rahat test suite — Miya + Sports Scientist agent testing.

Architected as five layers (per the L7 Test Architect review, 2026-05-08):

    1. Unit         — tests/test_*.py             — pure functions, no I/O
    2. Contract     — tests/test_miya_*.py        — Agent ABI, Charter ABI
    3. Eval         — tests/evals/test_*.py       — scenario fidelity (golden +
                                                     optional LLM-as-judge)
    4. Adversarial  — tests/evals/test_adv_*.py   — prompt injection, jailbreak,
                                                     persona drift, hallucinated math
    5. Regression   — tests/test_replay_*.py      — trace-replay diff vs.
                                                     last-known-good fixtures

All five layers run with `python -m tests.run_all` and emit a single
markdown summary. RAHAT_TEST_MODE=1 is forced in conftest so no test
can accidentally write to the live vault/rahat.db.
"""
