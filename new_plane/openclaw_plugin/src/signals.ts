/**
 * signals — cross-agent typed signal interface (TS side).
 *
 * Wraps the Python adapter's /signals/publish and /signals/recent. The
 * authoritative store is new_plane/signals/store.py (SQLite).
 *
 * Per the PM thesis v1.1, this is the LOAD-BEARING primitive. Every
 * agent publishes outcomes after a decision; consumers must call
 * mark_consumed (post-MVP) after folding a signal into their own
 * decision. See specs/RAHAT_PM_THESIS_v1_1_DELTA_2026-05-30.md §4 rule 6.
 */

import { adapterPost, adapterGet, newTraceId } from "./adapter_client.js";

export type SignalType =
  | "plan_delivered"
  | "wod_designed"
  | "wod_scaled"
  | "pace_check_emitted"
  | "user_thumbsup"
  | "user_thumbsdown"
  | "outcome_logged"
  | "miya_synthesized"
  | "miya_arbitration_resolved";

export interface PublishArgs {
  agent: "kobe" | "fraser" | "huberman" | "miya" | string;
  type: SignalType | string;
  payload: Record<string, unknown>;
  trace_id?: string;
}

export interface Signal {
  id: number;
  agent: string;
  type: string;
  payload: Record<string, unknown>;
  ts: string;
  trace_id: string;
  consumed_by: string[];
}

export async function publish(args: PublishArgs): Promise<number | null> {
  const r = await adapterPost<
    { trace_id: string; agent: string; type: string; payload: Record<string, unknown> },
    { signal_id: number }
  >("/signals/publish", {
    trace_id: args.trace_id ?? newTraceId("sig"),
    agent: args.agent,
    type: args.type,
    payload: args.payload,
  });
  return r.result?.signal_id ?? null;
}

export async function recent(opts?: { agent?: string; limit?: number }): Promise<Signal[]> {
  const q: Record<string, string | number> = {};
  if (opts?.agent) q.agent = opts.agent;
  if (opts?.limit) q.limit = opts.limit;
  const r = await adapterGet<{ items: Signal[] }>("/signals/recent", q);
  return r.result?.items ?? [];
}
