# Architecture diagrams

Standalone SVG files for ARB submission, Google Doc embedding, or
presentation use. Updated 2026-05-08 (v2.0) for the model-first reasoner
pivot and the SOTA memory architecture.

| File | Title | Used in §… of ARCHITECTURE.md |
|---|---|---|
| `01-three-plane-architecture.svg` | Target architecture: three planes (Control / Data / Runtime) with Miya as the orchestrator | §4 |
| `02-now-next-later-roadmap.svg` | Now / Next / Later — shipped vs deferred with trigger conditions | §6 |
| `03-routing-and-trace-flow.svg` | Routing & trace flow — one inbound message, end to end (legacy regex path) | §5, §8.3 |
| `04-memory-architecture.svg` | Memory architecture — four-tier hierarchy, agent adapters, sleep-time consolidation | §11 |
| `05-model-first-reasoner-flow.svg` | Model-first reasoner flow — Gemini 2.5 Flash + tool catalog + memory + voice + Telegram | §12 |
| `06-mesh-extensibility.svg` | Mesh extensibility — per-agent adapters over shared substrate; ~1 day to onboard each future agent | §13 |

## Why SVG (not PNG / Mermaid)

- **Scales without quality loss** — render at any zoom level for screen, print, or projector.
- **Embeddable in Google Docs** — Insert → Drawing → New → File → Upload SVG, or paste-as-image.
- **Diff-friendly** — text under version control; reviewers can comment on the source.
- **Self-contained** — no external dependencies, no Mermaid runtime, no theme rendering.

## How to use

**In a Google Doc / Slide:** Drag the SVG file into the document; it'll embed
as an image. For a PPT, convert with `rsvg-convert` or open in Figma/Sketch
and export PNG at 2x for projector clarity.

**In Notion:** Drag-and-drop the SVG; Notion renders it inline.

**In Markdown / GitHub:** Reference with `![Title](specs/diagrams/04-memory-architecture.svg)`.

**In a print PDF:** SVG embeds cleanly via most Markdown-to-PDF tools (pandoc,
Marked, Typora) at full resolution.

## Editing

Open in any text editor — these are hand-written SVG with named CSS classes
so colors and typography can be tuned in one place. Three accent colors used:

  - `.accent { fill: #d97757; }` — primary highlight (Rahat orange)
  - `.box-blue { fill: #f0f5fa; stroke: #6b88a8; }` — substrate / data layer
  - `.box-green { fill: #f1f6f0; stroke: #7a9270; }` — invariant blocks / contracts

To regenerate from source after edits to the underlying architecture, see
the corresponding markdown specs in `specs/`.

## Versioning

  - **v1.0 (May 2026)** — original three diagrams (01–03), Now-phase shipped, regex dispatcher live, single agent (Scientist).
  - **v2.0 (May 2026 mid-cycle)** — added 04–06 for the model-first reasoner pivot + mesh-wide memory architecture. Diagrams 01–03 remain accurate for the legacy regex path (still available behind `RAHAT_LEGACY_DISPATCH=1`).
