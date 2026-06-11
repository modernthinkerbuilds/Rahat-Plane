# OpenClaw integration — adaptation guide

**2026-05-31.** The one piece of weekend work that's still hand-effort:
adapting `new_plane/openclaw_plugin/src/index.ts` against the actual
OpenClaw plugin-SDK in `staging/fleet/`. Below are the exact API surfaces
observed in the vendored SDK and the concrete adaptation steps.

> **Why this is hand-work:** the OpenClaw plugin SDK doesn't ship a
> `definePlugin` factory. Plugins register themselves through side-effect
> calls (`registerContextEngine`) and by exporting tool factories. The
> registration pattern is conventional, not declared, so the right shape
> depends on how your OpenClaw build loads `extensions/*`.

---

## What the SDK actually exports

From `staging/fleet/src/plugin-sdk/index.ts`, the relevant types are
**re-exports** (you import from `openclaw/plugin-sdk`):

```ts
import type {
  ChannelAgentTool,
  ChannelAgentToolFactory,
} from "openclaw/plugin-sdk";

import {
  registerContextEngine,
} from "openclaw/plugin-sdk";  // re-exported from context-engine/registry
```

There is **no `Plugin`** type and **no `definePlugin`** factory. Adapt
your registration shape by directly invoking the runtime's expected
hooks.

---

## ChannelAgentTool — the tool contract

From `staging/fleet/src/channels/plugins/types.core.ts`:

```ts
export type ChannelAgentTool = AgentTool<TSchema, unknown> & {
  /* channel-specific extras */
};

export type ChannelAgentToolFactory =
  (params: { cfg?: OpenClawConfig }) => ChannelAgentTool[];
```

Each tool is an `AgentTool` — an object with a schema (input shape),
description, and a handler. **You expose a factory function** that
returns the array of tools when the runtime calls it during plugin load.

### Concrete shape — adapt our skeleton tools

Replace `new_plane/openclaw_plugin/src/tools/kobe.ts` with the
`ChannelAgentTool[]` shape. The existing functions in that file are
already the right handler bodies — they just need to be wrapped in the
tool envelope. Pattern:

```ts
// new_plane/openclaw_plugin/src/tools/kobe.ts
import { z } from "zod";
import type { ChannelAgentTool, ChannelAgentToolFactory } from "openclaw/plugin-sdk";
import { adapterPost, newTraceId } from "../adapter_client.js";

export const kobeToolsFactory: ChannelAgentToolFactory = () => [
  {
    name: "kobe_today_target",
    description: "Today's day-type + kcal target from Kobe (the Scientist).",
    schema: z.object({}),
    handler: async () => {
      return adapterPost("/kobe/today_target", { trace_id: newTraceId() });
    },
  },
  {
    name: "kobe_project_eta",
    description:
      "Inverse goal projection: given a fixed daily intake and weekly active-burn, " +
      "estimate when the user will hit a target weight.",
    schema: z.object({
      target_lbs: z.number().optional(),
      target_kg: z.number().optional(),
      daily_intake_kcal: z.number(),
      weekly_active_kcal: z.number(),
    }),
    handler: async (args) => {
      return adapterPost("/kobe/project_eta", { trace_id: newTraceId(), ...args });
    },
  },
  // ... one per endpoint
];
```

Notes:
- The actual `AgentTool` schema validator may be a different library
  (zod, valibot, or OpenClaw's own typebox flavor). Match what
  `staging/fleet/src/channels/plugins/agent-tools/whatsapp-login.ts`
  uses — that's the reference implementation pattern in the repo.
- Tool names get scoped by plugin; pick a stable prefix (`kobe_*`,
  `fraser_*`) so the runtime's tool registry doesn't collide.

Do the same for `fraser.ts`. Single tool: `fraser_design_session`.

---

## ContextEngine — optional but recommended

From `staging/fleet/src/context-engine/types.ts:68`, the interface is:

```ts
export interface ContextEngine {
  readonly info: ContextEngineInfo;        // { id, name, version?, ownsCompaction? }
  bootstrap?(params): Promise<BootstrapResult>;
  ingest(params): Promise<IngestResult>;
  ingestBatch?(params): Promise<IngestBatchResult>;
  afterTurn?(params): Promise<void>;
  assemble(params): Promise<AssembleResult>;
  compact(params): Promise<CompactResult>;
  prepareSubagentSpawn?(params): Promise<SubagentSpawnPreparation | undefined>;
}
```

**Required:** `info`, `ingest`, `assemble`, `compact`. Everything else
is optional.

Registration is a side-effect call from anywhere your plugin loads:

```ts
import { registerContextEngine } from "openclaw/plugin-sdk";

registerContextEngine("rahat-substrate", () => {
  return {
    info: { id: "rahat-substrate", name: "Rahat Substrate", version: "0.1.0" },
    async ingest({ sessionId, message }) {
      // Mirror the message into the signal store via the adapter.
      await publish({
        agent: "miya",
        type: "message_ingested",
        trace_id: sessionId,
        payload: { role: message.role, content_len: message.content?.length ?? 0 },
      });
      return { ok: true };
    },
    async assemble({ sessionId, messages, tokenBudget }) {
      // For v0: hand back the messages unchanged + an injected facts block
      // pulled from /kobe/today_target + /signals/recent.
      // Real causal-contribution ranking is week-3+ work.
      const facts = await fetchFactsBlock(sessionId);
      const factsMsg = { role: "system", content: facts };
      return { messages: [factsMsg, ...messages] };
    },
    async compact({ sessionId, tokenBudget }) {
      // v0: no-op; OpenClaw's own compaction stays in charge.
      return { compacted: false };
    },
  };
});
```

This is the seam that turns Rahat's deterministic substrate into a real
ContextEngine. v0 = facts injection at `assemble`. Week-3+ = causal-
contribution memory ranking through the signal store's `consumed_by`
data.

---

## Putting it together — what `src/index.ts` becomes

Replace the pseudo-shim in the skeleton with the real shape:

```ts
// new_plane/openclaw_plugin/src/index.ts
import { registerContextEngine } from "openclaw/plugin-sdk";
import { kobeToolsFactory } from "./tools/kobe.js";
import { fraserToolsFactory } from "./tools/fraser.js";
import { rahatSubstrateEngine } from "./context_engine.js";

// Side-effect: register the context engine on import.
registerContextEngine("rahat-substrate", rahatSubstrateEngine);

// Export tool factories — the runtime imports + calls them during load.
export const tools = [kobeToolsFactory, fraserToolsFactory];

// Convenience re-exports for tests and the simulator.
export { adapterPost, newTraceId } from "./adapter_client.js";
export { publish, recent } from "./signals.js";
export { chooseModel, instrument } from "./cost_router.js";
export { NewMiya } from "./agents/miya.js";
```

Where the runtime's plugin loader picks up `tools[]` and registers each
tool factory — verify against the exact OpenClaw version's plugin entry
contract.

---

## Concrete next steps for you

1. `cd new_plane/openclaw_plugin && npm install`.
2. Open `staging/fleet/src/channels/plugins/agent-tools/whatsapp-login.ts`
   to see the reference tool shape. Note the exact `AgentTool` schema
   library it uses (zod / typebox / etc.) — match it in our tools.
3. Adapt `src/tools/kobe.ts` and `src/tools/fraser.ts` from the
   thin-wrapper functions they are today into `ChannelAgentToolFactory`
   exports following the pattern above.
4. Create `src/context_engine.ts` with a minimal `ContextEngine` (the v0
   I sketched).
5. Replace `src/index.ts` with the real shape.
6. `npm run typecheck` — fix any type errors.
7. Wire into OpenClaw: typically by adding to `staging/fleet/extensions/`
   (verify the directory convention with `cat staging/fleet/CONTRIBUTING.md`).
8. Boot OpenClaw with the plugin registered, send a `/v2` Telegram
   message, watch the adapter logs.

---

## What stays the same

- `bridges/openclaw_adapters/` — Python side, no changes. Tested with
  real Kobe/Fraser code paths through the adapter; all green.
- `new_plane/signals/store.py` — the load-bearing primitive, unchanged.
- `src/adapter_client.ts`, `src/signals.ts`, `src/cost_router.ts`,
  `src/agents/miya.ts`, `src/agents/miya.system.md` — these are the
  plugin's *body* and stay as-is. The adaptation is purely the
  registration surface (`src/index.ts` + the tool factories' wrappers).

---

## If something doesn't fit

The OpenClaw plugin SDK may have evolved between the vendored
`staging/fleet/` snapshot and the version you ultimately ship. If
`ChannelAgentTool` looks different, the shape adaptation is local to
`src/tools/*.ts` — the rest of the plugin (HTTP client, signals, cost
router, miya orchestrator) is SDK-independent.

If the SDK truly diverges and the adaptation gets messy, fall back to
the **LangGraph + Letta + Python** plan — that's what's in the thesis
doc as the honest fallback. The Python adapter + signal store on this
side stay reusable either way.
