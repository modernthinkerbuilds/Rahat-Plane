"""agents.kipchoge — the running coach's engine (Kipchoge PRD v0.3, S0).

Numbers from code, words from the model. Everything here is
deterministic: the reads (`ingest`), the segments of a run
(`segments`), the metrics (`metrics`), the terrain (`terrain`), the
conditions (`conditions`), the classification and grade (`grade`), the
HR–pace model and the rolling estimate (`model`), and the evidence
records every value travels in (`records`). Every public function takes
its clock (`now`) and its data as arguments; nothing reads the wall
clock or the wire on its own.
"""
