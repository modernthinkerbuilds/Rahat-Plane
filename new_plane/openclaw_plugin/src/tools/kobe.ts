/**
 * kobe tools — TS-side tool definitions wrapping the Python adapter.
 *
 * Each tool is a thin HTTP call. The OpenClaw runtime exposes these to
 * new_miya as callable functions. Signatures kept narrow on purpose: the
 * adapter signature is the contract; this file is its TS face.
 */

import { adapterPost, newTraceId } from "../adapter_client.js";

export interface ToolContext {
  trace_id: string;
}

/** Today's day-type + kcal target. */
export async function kobe_today_target(ctx: ToolContext) {
  return adapterPost<{ trace_id: string }, unknown>(
    "/kobe/today_target", { trace_id: ctx.trace_id });
}

export async function kobe_active_goal(ctx: ToolContext) {
  return adapterPost<{ trace_id: string }, unknown>(
    "/kobe/active_goal", { trace_id: ctx.trace_id });
}

export async function kobe_pace(ctx: ToolContext) {
  return adapterPost<{ trace_id: string }, unknown>(
    "/kobe/pace", { trace_id: ctx.trace_id });
}

export async function kobe_recalibration(ctx: ToolContext) {
  return adapterPost<{ trace_id: string }, unknown>(
    "/kobe/recalibration", { trace_id: ctx.trace_id });
}

export async function kobe_missed_workouts(ctx: ToolContext) {
  return adapterPost<{ trace_id: string }, unknown>(
    "/kobe/missed_workouts", { trace_id: ctx.trace_id });
}

export async function kobe_goal_plan(
  ctx: ToolContext,
  args: { target_lbs?: number; target_kg?: number; target_date?: string },
) {
  return adapterPost<
    { trace_id: string; target_lbs?: number; target_kg?: number; target_date?: string },
    unknown
  >("/kobe/goal_plan", { trace_id: ctx.trace_id, ...args });
}

export async function kobe_project_eta(
  ctx: ToolContext,
  args: { target_lbs?: number; target_kg?: number; daily_intake_kcal: number; weekly_active_kcal: number },
) {
  return adapterPost<
    { trace_id: string; target_lbs?: number; target_kg?: number;
      daily_intake_kcal: number; weekly_active_kcal: number },
    unknown
  >("/kobe/project_eta", { trace_id: ctx.trace_id, ...args });
}

export async function kobe_charter_check(
  ctx: ToolContext,
  args: { kind: string; priority?: number; now_iso?: string },
) {
  return adapterPost<
    { trace_id: string; kind: string; priority?: number; now_iso?: string },
    { allowed: boolean; reason: string | null }
  >("/kobe/charter_check", { trace_id: ctx.trace_id, ...args });
}

export const KobeTools = {
  kobe_today_target,
  kobe_active_goal,
  kobe_pace,
  kobe_recalibration,
  kobe_missed_workouts,
  kobe_goal_plan,
  kobe_project_eta,
  kobe_charter_check,
};
