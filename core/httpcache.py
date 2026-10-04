"""core.httpcache — one HTTP GET, recorded (ADR-018 §2.10: replay is
deterministic from cassettes for model AND tool calls).

Tools keep their own code paths on replay (a page is parsed, facts are
written to the knowledge base exactly as they were live); only the
network response is a cassette, keyed on the URL. Under RAHAT_TEST_MODE
with no cassette and no recording the call never leaves the machine:
`HermeticMiss` is raised and the caller reports it.

    fetched = httpcache.get(url, headers={...}, timeout=10)
    fetched.status, fetched.final_url, fetched.content_type, fetched.body

A caller may pass `opener` (tests) to bypass the cassette entirely.
"""
from __future__ import annotations

import base64
import urllib.error
import urllib.request
from dataclasses import dataclass

from core import cassette

MAX_BYTES = 2_000_000
KIND = "http"


class HermeticMiss(RuntimeError):
    pass


@dataclass
class Fetched:
    url: str
    status: int = 0
    final_url: str = ""
    content_type: str = ""
    body: bytes = b""
    error: str | None = None
    from_cassette: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and 0 < self.status < 400

    def as_json(self) -> dict:
        return {"url": self.url, "status": self.status, "final_url": self.final_url, "content_type": self.content_type,
                "body_b64": base64.b64encode(self.body).decode("ascii"), "error": self.error}

    @classmethod
    def from_json(cls, url: str, d: dict) -> "Fetched":
        return cls(url=url, status=int(d.get("status") or 0), final_url=d.get("final_url") or url,
                   content_type=d.get("content_type") or "", body=base64.b64decode(d.get("body_b64") or ""),
                   error=d.get("error"), from_cassette=True)


def _real(url: str, headers: dict, timeout: float, opener=None) -> Fetched:
    req = urllib.request.Request(url, headers=headers)
    open_fn = opener or urllib.request.urlopen
    out = Fetched(url=url)
    try:
        with open_fn(req, timeout=timeout) as r:  # noqa: S310 - the caller checked the source policy
            out.status = int(getattr(r, "status", 200) or 200)
            out.final_url = getattr(r, "url", url) or url
            out.content_type = (r.headers.get("Content-Type", "") if hasattr(r, "headers") else "") or ""
            out.body = r.read(MAX_BYTES)
    except urllib.error.HTTPError as e:
        out.status, out.error = e.code, f"http {e.code}"
    except Exception as e:  # noqa: BLE001
        out.error = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def get(url: str, *, headers: dict | None = None, timeout: float = 10.0, opener=None,
        cassette_dir: str | None = None) -> Fetched:
    headers = headers or {}
    if opener is not None:
        return _real(url, headers, timeout, opener)
    key = {"http": url}
    hit = cassette.lookup(KIND, key, dir=cassette_dir)      # record on miss: a recorded page is reused
    if hit is not None:
        return Fetched.from_json(url, hit)
    if cassette.test_mode() and not cassette.recording():
        raise HermeticMiss(f"no http cassette for {url}")
    out = _real(url, headers, timeout)
    if cassette.recording() and not (out.error and not out.status):   # transport failures are not worth keeping
        cassette.save(KIND, key, out.as_json(), dir=cassette_dir)
    return out
