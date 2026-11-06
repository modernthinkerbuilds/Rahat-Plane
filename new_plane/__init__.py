"""new_plane — Rahat's runtime (ADR-013): Miya's runner, the signal store, the seams.

Per ``specs/ARCHITECT_THREADS_2026-05-30.md``, everything under this
directory is owned by the new-plane architect. The KTLO architect (Python
plane) does not touch it. Cross-plane interaction happens exclusively via:

  - ``bridges/http_adapter/`` (HTTP)
  - ``new_plane/signals/`` (typed cross-agent contract)

Everything here is Python: the signals, the schema, the helpers and the runner.
"""


# A private overlay's new_plane/<name>/ (core/overlay.py) is found here too.
from core import overlay as _overlay  # noqa: E402

__path__ = _overlay.extend(__path__, __name__)
