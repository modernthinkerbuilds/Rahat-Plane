/**
 * fraser tools — TS-side tool definitions wrapping the Python adapter.
 *
 * design_session is heavy (LLM). The Miya orchestrator must respect the
 * autonomy budget (≤ 1 design call per user message) and cache where
 * possible.
 */

import { adapterPost } from "../adapter_client.js";

export interface ToolContext {
  trace_id: string;
}

export async function fraser_design_session(
  ctx: ToolContext,
  args: { message: string; chat_id?: string },
) {
  return adapterPost<
    { trace_id: string; message: string; chat_id?: string },
    { text: string }
  >("/fraser/design_session", { trace_id: ctx.trace_id, ...args });
}

export const FraserTools = { fraser_design_session };
