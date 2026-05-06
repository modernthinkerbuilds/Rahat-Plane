# Architecture diagrams

Standalone SVG files for ARB submission, Google Doc embedding, or
presentation use.

| File | Title | Used in §… of ARCHITECTURE.md |
|---|---|---|
| `01-three-plane-architecture.svg` | Target architecture: three planes (Control / Data / Runtime) with Miya as the orchestrator | §4 |
| `02-now-next-later-roadmap.svg` | Now / Next / Later — shipped vs deferred with trigger conditions | §6 |
| `03-routing-and-trace-flow.svg` | Routing & trace flow — one inbound message, end to end | §5, §8.3 |

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

**In Markdown / GitHub:** Reference with `![Title](specs/diagrams/01-three-plane-architecture.svg)`.

**In a print PDF:** SVG embeds cleanly via most Markdown-to-PDF tools (pandoc,
Marked, Typora) at full resolution.

## Editing

Open in any text editor — these are hand-written SVG with named CSS classes
so colors and typography can be tuned in one place. To change the accent
color, find `.accent { fill: #d97757; }` at the top of each file and update
the hex.

To regenerate from source after edits to the underlying architecture, see
the `mcp__visualize__show_widget` calls in the conversation history that
produced these files.
