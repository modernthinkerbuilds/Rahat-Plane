"""core.harness — the pipeline every free-text message goes through
(ADR-018 §2.2): Understand → Contract → Execute → Verify → Judge →
Control → Render → Record.

Phase 1a ships the two schemas the rest stands on:
    frame     — the model's typed reading of a message, validated
    contract  — the outcome checklist code assembles from the frame and
                the agent's outcome library
The steps that call tools, verify, judge, control and render arrive in
Phase 1b on top of these.
"""
from core.harness.frame import Frame, FrameError, parse_frame, frame_json_schema  # noqa: F401
from core.harness.contract import (Contract, OutcomeLibrary, load_library,     # noqa: F401
                                   assemble)
