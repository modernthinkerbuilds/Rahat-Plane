"""ATS feed fetchers — Greenhouse / Ashby / Lever JSON + the NPAG page.

Hermetic rule (same as core.llm): under RAHAT_TEST_MODE=1 nothing here
may hit the wire — every fetch goes through an injected `http` seam or
raises. The seam is a callable `(method, url, json_body|None) -> object`
returning parsed JSON (dict/list) or text (str). Production uses
`requests_http()`.

Ashby note (Filter Config §4, learned live 2026-08-16): Ashby
rate-limits under concurrency — parallel board fetches return clean
200s with EMPTY arrays, which reads exactly like "no jobs today".
Callers must fetch Ashby boards serially; ingest.py does.
"""
from __future__ import annotations

import html as _html
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
ASHBY_URL = ("https://api.ashbyhq.com/posting-api/job-board/{token}"
             "?includeCompensation=true")
LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
NPAG_URL = "https://www.npag.com/current-searches"


class ParseFailed(RuntimeError):
    """Source page fetched but yielded nothing parseable — ledger this,
    never treat it as 'no jobs today' (the silent-zero failure mode)."""


def requests_http():
    """Production HTTP seam. Refuses to exist under test mode."""
    if os.getenv("RAHAT_TEST_MODE") == "1":
        raise RuntimeError(
            "no wire under RAHAT_TEST_MODE=1 — inject an http seam "
            "(mirrors the core.llm hermetic rule)")
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = (
        "rahat-benji/1.0 (personal job-search agent; contact via repo)")

    def _http(method: str, url: str, json_body=None):
        resp = session.request(method, url, json=json_body, timeout=30)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        return resp.json() if "json" in ctype else resp.text

    return _http


def strip_html(text: str) -> str:
    text = _html.unescape(text or "")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text,
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _date10(s: str | None) -> str | None:
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s))
    return m.group(1) if m else None


def fetch_greenhouse(token: str, http) -> list[dict]:
    data = http("GET", GREENHOUSE_URL.format(token=token))
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out = []
    for j in jobs:
        out.append({
            "title": (j.get("title") or "").strip(),
            "location": ((j.get("location") or {}).get("name") or "").strip(),
            "canonical_url": j.get("absolute_url") or "",
            "posted_date": _date10(j.get("first_published")
                                   or j.get("updated_at")),
            "jd_text": strip_html(j.get("content") or ""),
        })
    return out


def fetch_ashby(token: str, http) -> list[dict]:
    data = http("GET", ASHBY_URL.format(token=token))
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out = []
    for j in jobs:
        loc = (j.get("location") or "").strip()
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = f"Remote — {loc}" if loc else "Remote"
        comp = ""
        c = j.get("compensation") or {}
        tiers = c.get("compensationTierSummary") or ""
        if tiers:
            comp = str(tiers)
        out.append({
            "title": (j.get("title") or "").strip(),
            "location": loc,
            "canonical_url": j.get("jobUrl") or j.get("applyUrl") or "",
            "posted_date": _date10(j.get("publishedAt")),
            "jd_text": strip_html(j.get("descriptionHtml")
                                  or j.get("descriptionPlain") or ""),
            "comp_range": comp,
        })
    return out


def fetch_lever(token: str, http) -> list[dict]:
    data = http("GET", LEVER_URL.format(token=token))
    jobs = data if isinstance(data, list) else []
    out = []
    for j in jobs:
        created = j.get("createdAt")
        posted = None
        if isinstance(created, (int, float)) and created > 0:
            posted = datetime.fromtimestamp(
                created / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        cats = j.get("categories") or {}
        out.append({
            "title": (j.get("text") or "").strip(),
            "location": (cats.get("location") or "").strip(),
            "canonical_url": j.get("hostedUrl") or "",
            "posted_date": posted,
            "jd_text": strip_html(j.get("descriptionPlain")
                                  or j.get("description") or ""),
        })
    return out


class _NpagParser(HTMLParser):
    """Tolerant card scraper for npag.com/current-searches.

    Structure (verified 2026-08-17): listing cards carry the org in
    bold/heading text and the role title as a link (to an npag.com
    detail slug or an external ATS). We collect (heading, link) pairs in
    document order and pair each qualifying link with the nearest
    preceding heading."""

    SKIP_LINK_TEXT = {"learn more", "apply", "home", "about", "contact",
                      "current searches", "read more", "back", "search"}

    def __init__(self):
        super().__init__()
        self._events: list[tuple[str, str]] = []  # (kind, text/href)
        self._link_href: str | None = None
        self._buf: list[str] = []
        self._bold_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._link_href = dict(attrs).get("href") or ""
            self._buf = []
        elif tag in ("b", "strong", "h1", "h2", "h3", "h4"):
            self._bold_depth += 1
            self._buf = []

    def handle_data(self, data):
        self._buf.append(data)

    def handle_endtag(self, tag):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if tag == "a" and self._link_href is not None:
            if text:
                self._events.append(("link", f"{text}\t{self._link_href}"))
            self._link_href = None
        elif tag in ("b", "strong", "h1", "h2", "h3", "h4"):
            self._bold_depth = max(0, self._bold_depth - 1)
            if text:
                self._events.append(("head", text))
        self._buf = []

    def entries(self) -> list[dict]:
        out, last_head = [], ""
        for kind, payload in self._events:
            if kind == "head":
                last_head = payload
                continue
            text, _, href = payload.partition("\t")
            low = text.lower().strip()
            if (len(text) < 5 or len(text) > 90
                    or low in self.SKIP_LINK_TEXT
                    or href.startswith(("#", "mailto:"))):
                continue
            out.append({"title": text, "org": last_head or "NPAG search",
                        "canonical_url": href})
        return out


def fetch_npag(http) -> list[dict]:
    page = http("GET", NPAG_URL)
    if not isinstance(page, str):
        raise ParseFailed("npag returned non-text response")
    p = _NpagParser()
    p.feed(page)
    entries = p.entries()
    if len(entries) < 2:
        raise ParseFailed(f"npag parse yielded {len(entries)} entries — "
                          "page structure changed?")
    for e in entries:
        e.update({"location": "", "posted_date": None, "jd_text": "",
                  "comp_range": ""})
    return entries


PLATFORM_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
}
