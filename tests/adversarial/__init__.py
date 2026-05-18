"""Adversarial phrasings layer.

For every named user intent (lookup workout, show plan, log weight,
set tier, etc.), enumerate 10+ real phrasings (informal, typo-ridden,
multi-clause). Each phrasing routes to the CORRECT agent and produces
a NON-EMPTY answer.

This is the "user doesn't type like a robot" defense.

CONVENTION:
    Phrasings are MINED from the user's actual Telegram history
    (decisions.input_json for past messages over the last 30 days).
    Never invent phrasings — they have to be real, otherwise the
    layer becomes theater.

    A bootstrapping script lives at scripts/mine_phrasings.py that
    pulls real messages from the live DB into a JSON corpus.

    The corpus is checked into tests/adversarial/corpus.json and
    rebuilt on demand. Each entry is labeled with the intent it
    SHOULD have routed to, so the test can assert correctness.
"""
