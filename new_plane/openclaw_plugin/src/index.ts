/**
 * rahat-new-plane — OpenClaw plugin entry point.
 *
 * Exports the new_miya agent + its tools so the OpenClaw runtime can
 * register them. The actual registration shape depends on the OpenClaw
 * plugin SDK version — see the runtime's docs for the exact API and
 * adapt the bottom of this file. The CORE logic (tools, miya, signals,
 * cost router) is independent and stays.
 */

export * from "./adapter_client.js";
export * from "./signals.js";
export * from "./cost_router.js";
export { KobeTools } from "./tools/kobe.js";
export { FraserTools } from "./tools/fraser.js";
export { NewMiya } from "./agents/miya.js";

/**
 * Plugin registration shim. Edit when wiring against the specific
 * OpenClaw plugin SDK version. Reference:
 *   - `staging/fleet/src/plugin-sdk/index.ts` exports `ChannelAgentTool`,
 *     `ChannelAgentToolFactory`, `ContextEngine`, `registerContextEngine`.
 *
 * Pseudo-shape (uncomment + adapt to real SDK signatures):
 *
 *   import type { Plugin } from "openclaw/plugin-sdk";
 *   import { KobeTools, FraserTools, NewMiya } from "./index.js";
 *
 *   const plugin: Plugin = {
 *     name: "rahat-new-plane",
 *     agents: { new_miya: NewMiya },
 *     tools: { ...KobeTools, ...FraserTools },
 *   };
 *   export default plugin;
 */
