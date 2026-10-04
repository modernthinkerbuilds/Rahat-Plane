"""core.fetch — a robots-compliant page read that returns data, never
instructions (ADR-018 §2.7; platform PR-D2, PR-D6, PR-V3).

`fetch_page(url)` asks the source registry first; a blocked or
link-only host is refused before any request. A 403 or 429 blocks the
host for the day. The page is reduced to text (stdlib HTML parser),
instruction-shaped lines are neutralized, and the result carries the
fetch time so every fact read from it can cite "url + read time".

The web is data. Nothing read here can change a contract, write state,
send a message or call a tool: the executor only ever sees the `text`
field as a tool result, and `sanitize()` strips the phrasings that try
to look like instructions.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

from core import httpcache, sources

MAX_BYTES = 1_500_000
DEFAULT_MAX_CHARS = 20_000

_INSTRUCTION_RE = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|prompts?)|"
    r"you are (now |an? )|as an ai|system prompt|\bsystem:\s|\bassistant:\s|"
    r"disregard (the|your) (rules|instructions)|do not tell the user|"
    r"call the (tool|function)|<\s*/?\s*(script|style|iframe)\b|"
    r"\bprompt injection\b|\bjailbreak\b|BEGIN (SYSTEM|INSTRUCTIONS))", re.I)


def sanitize(text: str) -> tuple[str, int]:
    """Neutralize instruction-shaped lines. Returns (text, lines_removed)."""
    out, removed = [], 0
    for line in text.splitlines():
        if _INSTRUCTION_RE.search(line):
            removed += 1
            continue
        out.append(line)
    return "\n".join(out), removed


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "iframe", "template"}
    BLOCK = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "header", "footer", "td", "th", "dd", "dt", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data      # the title is its own field, not body text
            return
        self.parts.append(data)

    def handle_comment(self, data):
        return  # comments are a favourite place for instructions; dropped


def html_to_text(raw: str) -> tuple[str, str]:
    p = _Text()
    try:
        p.feed(raw)
    except Exception:  # noqa: BLE001 - a malformed page still yields what was parsed
        pass
    text = html.unescape("".join(p.parts))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text, p.title.strip()


@dataclass
class Page:
    url: str
    ok: bool
    status: int = 0
    final_url: str = ""
    title: str = ""
    text: str = ""
    fetched_at: str = ""
    policy: str = ""
    error: str | None = None
    lines_removed: int = 0
    trust_tier: str = ""

    def as_tool_value(self, max_chars: int = DEFAULT_MAX_CHARS) -> dict:
        return {"url": self.url, "final_url": self.final_url or self.url, "ok": self.ok,
                "status": self.status, "title": self.title, "fetched_at": self.fetched_at,
                "trust_tier": self.trust_tier, "error": self.error,
                "text": self.text[:max_chars] + ("…[truncated]" if len(self.text) > max_chars else "")}


def fetch_page(url: str, *, timeout: float = 10.0, max_chars: int = DEFAULT_MAX_CHARS,
               now: datetime | None = None, opener=None) -> Page:
    """Read one page as text under the source registry's rules."""
    fetched_at = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    pol = sources.policy_for(url)
    page = Page(url=url, ok=False, fetched_at=fetched_at, policy=pol["policy"],
                trust_tier=pol.get("trust_tier", ""))
    if pol["policy"] in ("blocked", "link_only"):
        page.error = f"{pol['policy']}: {pol['why']}"
        return page
    sources.throttle(url)
    headers = {"User-Agent": sources.user_agent(),
               "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"}
    try:
        fetched = httpcache.get(url, headers=headers, timeout=timeout, opener=opener)
    except httpcache.HermeticMiss:
        page.error = "hermetic: no cassette for this page"
        return page
    page.status, page.final_url = fetched.status, fetched.final_url or url
    if fetched.error:
        page.error = fetched.error
        if fetched.status in (403, 429):
            sources.block_for_today(url, f"http {fetched.status}")
            page.error = f"blocked today: http {fetched.status}"
        return page
    ctype, raw = fetched.content_type, fetched.body
    try:
        body = raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        body = str(raw)
    if "html" in ctype or body.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        text, title = html_to_text(body)
    else:
        text, title = body, ""
    text, removed = sanitize(text)
    page.text, page.title, page.lines_removed = text[:max_chars * 4], title, removed
    page.ok = page.status < 400 and bool(page.text.strip())
    if not page.ok and not page.error:
        page.error = "empty page"
    return page
