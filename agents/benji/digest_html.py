"""benji.digest_html — the same digest, as an email-client-safe HTML
table (co-owner request, day one live: "packed neatly in a table").

Render-only: takes the structures build_morning already computed and
lays them out. The plain-text part remains the source of truth for
pins and text-only clients; this is the multipart/alternative sibling.
Inline styles only (email clients strip <style> blocks); no external
assets; degrades to the text part everywhere else.
"""
from __future__ import annotations

import json as _json
from datetime import datetime
from html import escape

FONT = ("font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',"
        "Roboto,Helvetica,Arial,sans-serif;")
TD = ("padding:6px 10px;border-bottom:1px solid #e8e8e8;"
      "font-size:13px;vertical-align:top;")
TH = ("padding:6px 10px;border-bottom:2px solid #444;font-size:11px;"
      "text-align:left;text-transform:uppercase;letter-spacing:.04em;"
      "color:#555;")
BAND_STYLES = {
    "apply": ("#0a7a33", "APPLY — full package attached"),
    "worth_a_look": ("#a36a00", "WORTH A LOOK — reply `kit ID` for the "
                                "package"),
    "maybe": ("#555555", "MAYBE"),
}


def _days_ago(posted: str | None, now: datetime) -> str:
    if not posted:
        return "—"
    try:
        d = (now.date() - datetime.strptime(posted, "%Y-%m-%d").date()).days
        return "today" if d <= 0 else f"{d}d ago"
    except ValueError:
        return "—"


def _row_html(r: dict, now: datetime, org_types: dict) -> str:
    match = (f"{int((r.get('coverage') or 0) * 100)}%"
             if (r.get("jd_text") or "").strip() else "n/a")
    flags = _json.loads(r.get("flags") or "[]")
    title = escape(r.get("title") or "")
    url = escape(r.get("canonical_url") or "", quote=True)
    title_cell = (f'<a href="{url}" style="color:#1a56b0;'
                  f'text-decoration:none;">{title}</a>' if url else title)
    note = escape("; ".join(flags)) if flags else ""
    org_type = escape((org_types.get(r.get("org", "")) or "")
                      .replace("_", " "))
    return (
        "<tr>"
        f'<td style="{TD}color:#999;">{r["id"]}</td>'
        f'<td style="{TD}">{title_cell}'
        + (f'<div style="font-size:11px;color:#b00;">⚑ {note}</div>'
           if note else "")
        + "</td>"
        f'<td style="{TD}">{escape(r.get("org") or "")}'
        + (f'<div style="font-size:11px;color:#888;">{org_type}</div>'
           if org_type else "")
        + "</td>"
        f'<td style="{TD}text-align:right;font-weight:600;">'
        f'{r.get("score") or "—"}</td>'
        f'<td style="{TD}text-align:right;">{match}</td>'
        f'<td style="{TD}">{escape(r.get("comp_range") or "—")}</td>'
        f'<td style="{TD}white-space:nowrap;">'
        f'{_days_ago(r.get("posted_date"), now)}</td>'
        "</tr>")


def _table(rows: list[dict], now: datetime, org_types: dict) -> str:
    head = "".join(f'<th style="{TH}">{h}</th>' for h in
                   ("#", "Role", "Org", "Score", "Match", "Comp",
                    "Posted"))
    body = "".join(_row_html(r, now, org_types) for r in rows)
    return (f'<table style="border-collapse:collapse;width:100%;'
            f'margin:4px 0 14px 0;">'
            f"<tr>{head}</tr>{body}</table>")


def render_morning_html(*, now: datetime, bands: dict, seen_count: int,
                        flagged: list[dict], sample: list[dict],
                        ledger: list[dict], warnings: list[str],
                        org_types: dict, first_morning: bool,
                        overflow_count: int,
                        package_names: list[str] | None) -> str:
    parts = [f'<div style="{FONT}max-width:860px;">',
             f'<h2 style="{FONT}font-size:17px;margin:0 0 2px 0;">'
             f'Benji — morning queue · {now.strftime("%a %Y-%m-%d")}'
             "</h2>"]
    if first_morning:
        parts.append(f'<p style="{FONT}font-size:12px;color:#666;'
                     f'margin:2px 0;">first run: top roles below; '
                     f'{overflow_count} more in initial_backlog.md '
                     "(attached)</p>")
    for band in ("apply", "worth_a_look", "maybe"):
        rows = bands.get(band) or []
        if not rows:
            continue
        color, label = BAND_STYLES[band]
        extra = ""
        if band == "apply" and package_names:
            extra = (f' · {len(package_names)} package(s) attached: '
                     + escape(", ".join(package_names)))
        parts.append(f'<h3 style="{FONT}font-size:13px;color:{color};'
                     f'margin:14px 0 2px 0;">{label} · {len(rows)}'
                     f'{extra}</h3>')
        parts.append(_table(rows, now, org_types))
    if seen_count:
        parts.append(f'<p style="{FONT}font-size:12px;color:#888;">'
                     f'+ {seen_count} more under 45 — reply '
                     "<b>expand</b> for the full list</p>")
    if not any(bands.values()):
        parts.append(f'<p style="{FONT}font-size:13px;">No new roles '
                     "since the last morning queue — the ledger below "
                     "says when each feed was checked.</p>")
    if sample:
        parts.append(f'<h3 style="{FONT}font-size:13px;margin:14px 0 '
                     '2px 0;">Sunday rejects sample (filter '
                     "calibration)</h3><ul style='margin:4px 0;'>")
        parts += [f'<li style="{FONT}font-size:12px;color:#666;">'
                  f'[{s["id"]}] {escape(s["title"])} — '
                  f'{escape(s["org"])}: {escape(s["reject_reason"])}'
                  "</li>" for s in sample]
        parts.append("</ul>")
    parts.append(f'<h3 style="{FONT}font-size:12px;color:#888;'
                 'margin:16px 0 2px 0;">Coverage ledger</h3>'
                 f'<p style="{FONT}font-size:11px;color:#999;'
                 'line-height:1.5;margin:2px 0;">')
    parts += [f'{escape(s["source"])}: {escape(s["state"])} '
              f'({s["last_count"]})'
              + (f' — {escape(s["note"])}' if s.get("note") else "")
              + "<br>" for s in ledger]
    parts.append("</p>")
    for w in warnings:
        parts.append(f'<p style="{FONT}font-size:12px;color:#b00;">⚠ '
                     f"{escape(w)}</p>")
    parts.append(f'<p style="{FONT}font-size:11px;color:#aaa;">reply: '
                 "applied N · skip N · kit N · snooze N [days] · "
                 "threshold N · pause · status · expand · help</p>"
                 "</div>")
    return "".join(parts)


def render_evening_html(*, now: datetime, rows: list[dict],
                        org_types: dict) -> str:
    return (f'<div style="{FONT}max-width:860px;">'
            f'<h2 style="{FONT}font-size:16px;margin:0;">Benji — '
            f'evening delta · {now.strftime("%a %Y-%m-%d")}</h2>'
            f'<p style="{FONT}font-size:12px;color:#666;">'
            f"{len(rows)} new role(s) scored 60+ since this morning — "
            "they'll be in tomorrow's queue too. Reply `kit ID` for a "
            "package now.</p>"
            + _table(rows, now, org_types) + "</div>")
