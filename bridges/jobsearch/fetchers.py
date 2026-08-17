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
    """HTML → text, PRESERVING block boundaries as newlines. The first
    version collapsed everything to one line, which silently disabled
    the coverage module's section weighting for every HTML source —
    'required qualifications' can only be found at a line start."""
    text = _html.unescape(text or "")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text,
                  flags=re.S | re.I)
    text = re.sub(r"</(p|div|li|ul|ol|h[1-6]|tr|section|article|"
                  r"blockquote)>|<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = "\n".join(re.sub(r"[ \t]+", " ", ln).strip()
                     for ln in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_NPAG_CHROME = re.compile(
    r"^(skip to content|open menu|close menu|folder:|back$|about us|"
    r"our team|our values|executive search|current searches|consulting|"
    r"insights|join our network|connect with us|npag\b|0$)", re.I)
_NPAG_TAIL = re.compile(r"^(meet the team|© ?copyright|connect with us|"
                        r"info@npag\.com|local:|toll free:)", re.I)


def npag_detail_text(page: str) -> str:
    """Isolate the position description from an NPAG detail page: drop
    nav/menu chrome line-by-line and cut the staff-bio/footer tail. The
    raw page inflated the coverage denominator with menus, phone numbers
    and recruiter bios — a 37% 'match' on a near-ideal role (caught live
    2026-08-17)."""
    lines = strip_html(page).splitlines()
    out: list[str] = []
    for ln in lines:
        if _NPAG_TAIL.match(ln.strip()):
            break
        if _NPAG_CHROME.match(ln.strip()) or re.match(
                r"^meet [A-Z][a-z]+$", ln.strip()):
            continue
        out.append(ln)
    return "\n".join(out).strip()


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
            out.append({"title": text, "org": last_head,
                        "canonical_url": href})
        return out


def fetch_npag(http, *, include_detail: bool = True) -> list[dict]:
    from urllib.parse import urljoin

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
        # Page hrefs are often relative ("/hewlett-paed") — the canonical
        # URL is the application destination, so join against the page
        # base (first-live-run lesson, 2026-08-17).
        e["canonical_url"] = urljoin(NPAG_URL, e["canonical_url"])
        e["org"] = e.get("org") or "NPAG search"
        e.update({"location": "", "posted_date": None, "jd_text": "",
                  "comp_range": ""})
        # S2 enrichment: the npag.com detail page carries the full
        # position description — without it, coverage can't compute for
        # exactly her highest-value channel and no package can build.
        # External-ATS links are left alone; a failed detail fetch
        # degrades to empty JD (kept, flagged), never drops the entry.
        if include_detail and "npag.com" in e["canonical_url"]:
            try:
                detail = http("GET", e["canonical_url"])
                if isinstance(detail, str):
                    e["jd_text"] = npag_detail_text(detail)[:15000]
            except Exception:                     # noqa: BLE001
                pass
    return entries


MICROSOFT_URL = ("https://gcsservices.careers.microsoft.com/search/api/"
                 "v1/search?q={query}&l=en_us&pg=1&pgSz=50")
WORKDAY_URL = ("https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/"
               "{tenant}/{site}/jobs")


def fetch_microsoft(token: str, http) -> list[dict]:
    """Microsoft careers JSON (Tara #2: Microsoft Philanthropies posts on
    the parent board). `token` is the search query — the collapse rule's
    keyword tag. The big-tech mission-word-in-title gate still applies
    downstream; this query just narrows the firehose."""
    from urllib.parse import quote

    data = http("GET", MICROSOFT_URL.format(query=quote(token)))
    jobs = (((data or {}).get("operationResult") or {}).get("result")
            or {}).get("jobs", []) if isinstance(data, dict) else []
    out = []
    for j in jobs:
        props = j.get("properties") or {}
        locs = props.get("locations") or []
        out.append({
            "title": (j.get("title") or "").strip(),
            "location": (locs[0] if locs else ""),
            "canonical_url": ("https://jobs.careers.microsoft.com/"
                              f"global/en/job/{j.get('jobId', '')}"),
            "posted_date": _date10(j.get("postingDate")),
            "jd_text": strip_html(props.get("description") or ""),
        })
    return out


def fetch_workday(token: str, http) -> list[dict]:
    """Generic Workday CXS board (verified live for Salesforce
    2026-08-17). `token` = "tenant/host/site/query"."""
    parts = token.split("/")
    if len(parts) < 3:
        raise ParseFailed(f"workday token needs tenant/host/site[/query],"
                          f" got: {token}")
    tenant, host, site = parts[0], parts[1], parts[2]
    query = parts[3] if len(parts) > 3 else ""
    base = f"https://{tenant}.{host}.myworkdayjobs.com"
    data = http("POST", WORKDAY_URL.format(tenant=tenant, host=host,
                                           site=site),
                {"searchText": query, "limit": 20, "offset": 0,
                 "appliedFacets": {}})
    postings = data.get("jobPostings", []) if isinstance(data, dict) \
        else []
    out = []
    for j in postings:
        posted = None
        m = re.search(r"posted (\d+)\+? days? ago",
                      (j.get("postedOn") or "").lower())
        if "today" in (j.get("postedOn") or "").lower():
            posted = "TODAY"          # resolved by caller against `now`
        out.append({
            "title": (j.get("title") or "").strip(),
            "location": (j.get("locationsText") or "").strip(),
            "canonical_url": base + (j.get("externalPath") or ""),
            "posted_date": posted,
            "jd_text": "",            # listing API has no JD; the title
                                      # gate + mission word still apply
            "_posted_days_ago": int(m.group(1)) if m else None,
        })
    return out


_GENERIC_HEADINGS = re.compile(
    r"^(our searches|active\s*searches|current searches|open searches|"
    r"quick links|functional specialization|expertise|npag search)$", re.I)


def fetch_searchfirm(url: str, http, *, firm: str = "search firm",
                     min_entries: int = 2) -> list[dict]:
    """Generic Tier-3 search-firm page: same tolerant card scrape as
    NPAG (headings = org, links = titles), no detail enrichment (page
    structures unknown; the jd-less kit flow handles it honestly).
    Yields ParseFailed → ledger, never a silent zero."""
    from urllib.parse import urljoin

    page = http("GET", url)
    if not isinstance(page, str):
        raise ParseFailed("search-firm page returned non-text")
    p = _NpagParser()
    p.feed(page)
    entries = p.entries()
    # Titles on these pages must look like ROLES, not nav: require the
    # noun/level shape the filter also uses (cheap pre-screen).
    role_like = [e for e in entries
                 if re.search(r"(director|officer|manager|associate|"
                              r"lead|president|coordinator|specialist|"
                              r"advisor|counsel|head)", e["title"], re.I)]
    if len(role_like) < min_entries:
        raise ParseFailed(f"parse yielded {len(role_like)} role-like "
                          f"entries from {len(entries)} links — page "
                          "structure changed?")
    for e in role_like:
        e["canonical_url"] = urljoin(url, e["canonical_url"])
        # "Title – Org" combined in the link text (Armstrong's shape):
        # split it; a generic heading ("Our searches") is noise, not an
        # org — fall back to "<firm> search".
        m = re.match(r"(.+?)\s+[–—-]\s+(.+)", e["title"])
        if m and len(m.group(2)) > 3:
            e["title"], e["org"] = m.group(1).strip(), m.group(2).strip()
        if not (e.get("org") or "").strip() or _GENERIC_HEADINGS.match(
                e["org"].strip()):
            e["org"] = f"{firm} search"
        e.update({"location": "", "posted_date": None, "jd_text": "",
                  "comp_range": ""})
    return role_like


PLATFORM_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "microsoft": fetch_microsoft,
    "workday": fetch_workday,
}
