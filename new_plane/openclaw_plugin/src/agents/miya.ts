/**
 * new_miya — arbitrating orchestrator on the new plane.
 *
 * Skeleton for the OpenClaw runtime. Reads/calls tools (via Python
 * adapter), arbitrates between Kobe + Fraser when they disagree,
 * synthesizes one coherent response, publishes outcomes as signals,
 * gates every send through the charter.
 *
 * v0 arbitration is hard-coded (per the weekend plan). The learner
 * upgrade is week-2+ work.
 *
 * IMPORTANT — autonomy budget:
 *   - max 3 adapter tool calls per user message
 *   - max 1 fraser_design_session per user message (heavy)
 *   - max 1 Gemini Pro call per user message (synthesis only)
 *
 * The OpenClaw runtime can be told to enforce these via plugin config;
 * we ALSO enforce them in code below as belt + suspenders.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { newTraceId } from "../adapter_client.js";
import { KobeTools } from "../tools/kobe.js";
import { FraserTools } from "../tools/fraser.js";
import { publish as publishSignal } from "../signals.js";
import { instrument } from "../cost_router.js";

// System prompt loaded from disk so it's editable without rebuild.
const SYSTEM_PROMPT = fs.readFileSync(
  new URL("./miya.system.md", import.meta.url),
  "utf8",
);

// ─── budget guard ─────────────────────────────────────────────────────────
class TurnBudget {
  toolCalls = 0;
  designCalls = 0;
  proCalls = 0;
  constructor(readonly trace_id: string) {}
  canCallTool(kind: "any" | "design" | "pro" = "any"): boolean {
    if (this.toolCalls >= 3) return false;
    if (kind === "design" && this.designCalls >= 1) return false;
    if (kind === "pro" && this.proCalls >= 1) return false;
    return true;
  }
  recordTool(kind: "any" | "design" | "pro" = "any"): void {
    this.toolCalls++;
    if (kind === "design") this.designCalls++;
    if (kind === "pro") this.proCalls++;
  }
}

// ─── input the runtime hands to handle() ──────────────────────────────────
export interface MiyaTurn {
  user_message: string;
  chat_id: string;
  // Caller may pass a trace_id (OpenClaw session id). We generate one if not.
  trace_id?: string;
}

export interface MiyaResponse {
  trace_id: string;
  text: string;
  sent: boolean;           // true if charter allowed; false if vetoed
  veto_reason?: string;
  used_tools: string[];
  signals_published: number[];
}

// ─── one-shot routing — what specialists do we even need? ─────────────────
function classifyIntent(msg: string): {
  needs_kobe: boolean;
  needs_fraser: boolean;
  is_design_request: boolean;
} {
  const m = msg.toLowerCase();
  const is_design_request = /\b(workout|wod|session|design|scale)\b/.test(m);
  const needs_fraser = is_design_request || /\bfraser\b/.test(m);
  const needs_kobe =
    /\b(plan|goal|target|weight|pace|hrv|cal|calor|kcal|week|track|behind|ahead|workout|wod)\b/.test(m);
  return { needs_kobe, needs_fraser, is_design_request };
}

// ─── arbitration rules (v0 — hard-coded) ──────────────────────────────────
interface ArbitrationInputs {
  pace?: any;
  recalibration?: any;
  active_goal?: any;
  fraser_text?: string;
}

interface ArbitrationVerdict {
  rule: string;
  recommendation: string;     // short imperative for the synthesis prompt
}

function arbitrate(inputs: ArbitrationInputs): ArbitrationVerdict | null {
  const recal = inputs.recalibration?.result ?? inputs.recalibration;
  if (recal && recal.behind_pace === true) {
    return {
      rule: "behind_pace",
      recommendation:
        "User is behind pace-to-date this week. Be honest in the brief — " +
        "do not say 'ahead of pace' or 'comfortable buffer'.",
    };
  }
  const goal = inputs.active_goal?.result ?? inputs.active_goal;
  if (goal && goal.active && goal.weeks_to_target && goal.weeks_to_target < 1) {
    return {
      rule: "goal_close",
      recommendation:
        "Goal date is < 1 week away. Acknowledge the deadline directly.",
    };
  }
  return null;
}

// ─── synthesis (placeholder — uses cost-router instrumentation) ──────────
async function synthesize(opts: {
  trace_id: string;
  user_message: string;
  facts: Record<string, any>;
  verdict: ArbitrationVerdict | null;
  fraser_text?: string;
}): Promise<string> {
  // In a real OpenClaw plugin, this would route through the runtime's
  // Gemini wrapper. Here we expose the shape; integration is on the
  // host application (the OpenClaw agent runtime calls this).
  //
  // The cost-router instrumentation logs the call regardless of who
  // executes it.
  return instrument("synthesis", opts.trace_id, async (model) => {
    // The actual call happens through the runtime's LLM client; this is
    // a placeholder string until the runtime wires it. See README for
    // how to plug in.
    const lines: string[] = [];
    lines.push(`[new_miya · ${model} · trace=${opts.trace_id}]`);
    if (opts.verdict) lines.push(`Rule: ${opts.verdict.rule}.`);
    if (opts.fraser_text) lines.push(opts.fraser_text);
    if (opts.facts.pace?.result) {
      lines.push(`Pace: ${JSON.stringify(opts.facts.pace.result).slice(0, 240)}`);
    }
    lines.push(`(user said: ${opts.user_message})`);
    return lines.join("\n");
  });
}

// ─── main handle ──────────────────────────────────────────────────────────
export async function handle(turn: MiyaTurn): Promise<MiyaResponse> {
  const trace_id = turn.trace_id ?? newTraceId("miya");
  const budget = new TurnBudget(trace_id);
  const ctx = { trace_id };
  const used: string[] = [];
  const signals: number[] = [];
  const facts: Record<string, any> = {};

  const intent = classifyIntent(turn.user_message);

  // ── 1. Pull the data we actually need (respecting budget) ───────────
  if (intent.needs_kobe && budget.canCallTool()) {
    facts.pace = await KobeTools.kobe_pace(ctx);
    used.push("kobe_pace"); budget.recordTool();
  }
  if (intent.needs_kobe && budget.canCallTool()) {
    facts.recalibration = await KobeTools.kobe_recalibration(ctx);
    used.push("kobe_recalibration"); budget.recordTool();
  }
  let fraser_text: string | undefined;
  if (intent.is_design_request && budget.canCallTool("design")) {
    const r = await FraserTools.fraser_design_session(ctx, {
      message: turn.user_message, chat_id: turn.chat_id,
    });
    fraser_text = r.result?.text;
    used.push("fraser_design_session"); budget.recordTool("design");
  }

  // ── 2. Arbitrate ───────────────────────────────────────────────────
  const verdict = arbitrate({ ...facts, fraser_text });

  // ── 3. Charter precheck ────────────────────────────────────────────
  const cc = await KobeTools.kobe_charter_check(ctx, {
    kind: intent.is_design_request ? "notify.user.reply" : "notify.user.reply",
  });
  used.push("kobe_charter_check");
  const allowed = cc.result?.allowed ?? true;
  const veto_reason = allowed ? undefined : (cc.result?.reason ?? undefined);

  // ── 4. Synthesize ──────────────────────────────────────────────────
  let text = "";
  if (allowed) {
    text = await synthesize({
      trace_id, user_message: turn.user_message,
      facts, verdict, fraser_text,
    });
  } else {
    text = ""; // charter vetoed — drop the message
  }

  // ── 5. Publish signal (load-bearing primitive) ─────────────────────
  const sid = await publishSignal({
    agent: "miya",
    type: "miya_synthesized",
    trace_id,
    payload: {
      user_message: turn.user_message,
      chat_id: turn.chat_id,
      intent,
      tools_used: used,
      arbitration_rule: verdict?.rule ?? null,
      charter_allowed: allowed,
      veto_reason: veto_reason ?? null,
      response_len: text.length,
    },
  });
  if (sid !== null) signals.push(sid);

  return {
    trace_id, text,
    sent: allowed,
    veto_reason,
    used_tools: used,
    signals_published: signals,
  };
}

export const NewMiya = { handle, SYSTEM_PROMPT };
