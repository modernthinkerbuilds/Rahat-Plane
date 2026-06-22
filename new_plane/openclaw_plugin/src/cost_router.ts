/**
 * cost_router — v0 instrumentation, not learning yet.
 *
 * Per the PM thesis v1.1, capability #1 is the *engine* powering five
 * immediate-decision surfaces (model routing among them). v0 is static:
 * a coarse policy that picks Flash for routine intents and Pro for
 * synthesis. Every call is logged with model/intent/latency/response so
 * the learner (week 2+) has data to train on.
 *
 * Log line shape (one JSON per Gemini call):
 *   {"ts","trace_id","intent","model","latency_ms","prompt_tokens","completion_tokens"}
 *
 * Read those lines back from cost_router.log to train the bandit.
 */

import * as fs from "node:fs";
import * as path from "node:path";

const LOG_PATH =
  process.env.OPENCLAW_COST_LOG ?? path.join(process.env.HOME ?? ".", ".rahat", "cost_router.log");

export type Intent =
  | "synthesis"          // Miya's executive synthesis call (Pro-only in v0)
  | "routine_lookup"     // Plain tool-mediated answer
  | "refinement"         // "shorter / swap x"
  | "design"             // Fraser-style composition (caller's tier choice)
  | "classification";    // Routing classifier

export type Model = "gemini-2.5-flash" | "gemini-2.5-pro";

const STATIC_POLICY: Record<Intent, Model> = {
  synthesis:       "gemini-2.5-pro",
  routine_lookup:  "gemini-2.5-flash",
  refinement:      "gemini-2.5-flash",
  design:          "gemini-2.5-pro",
  classification:  "gemini-2.5-flash",
};

export function chooseModel(intent: Intent): Model {
  return STATIC_POLICY[intent] ?? "gemini-2.5-flash";
}

export interface CostEvent {
  ts: string;
  trace_id: string;
  intent: Intent;
  model: Model;
  latency_ms: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  notes?: string;
}

let _ensured = false;

function ensureLogDir(): void {
  if (_ensured) return;
  try { fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true }); } catch {}
  _ensured = true;
}

export function log(event: CostEvent): void {
  ensureLogDir();
  try {
    fs.appendFileSync(LOG_PATH, JSON.stringify(event) + "\n");
  } catch (e) {
    // Never let logging crash a turn.
    console.warn(`[cost_router] log failed: ${(e as Error).message}`);
  }
}

/** Convenience wrapper: time a Gemini call and emit the cost event. */
export async function instrument<T>(
  intent: Intent,
  trace_id: string,
  fn: (model: Model) => Promise<T>,
): Promise<T> {
  const model = chooseModel(intent);
  const started = Date.now();
  try {
    const out = await fn(model);
    log({ ts: new Date().toISOString(), trace_id, intent, model, latency_ms: Date.now() - started });
    return out;
  } catch (e) {
    log({
      ts: new Date().toISOString(),
      trace_id, intent, model,
      latency_ms: Date.now() - started,
      notes: `error: ${(e as Error).message}`,
    });
    throw e;
  }
}
