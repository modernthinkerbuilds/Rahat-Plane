/**
 * adapter_client — HTTP client for bridges/openclaw_adapters/server.py.
 *
 * The contract surface between the new plane (TS) and the old plane
 * (Python). See specs/ARCHITECT_THREADS_2026-05-30.md for the rules:
 *   - reads only; writes go through the old plane's own handlers.
 *   - schema changes require coordination with KTLO architect.
 *
 * All requests carry a trace_id so the OpenClaw session and the Python
 * plane share a correlation key for by_trace() lookups.
 */

const ADAPTER_BASE = process.env.OPENCLAW_ADAPTER_URL ?? "http://127.0.0.1:8765";
const ADAPTER_TOKEN = (process.env.OPENCLAW_ADAPTER_TOKEN ?? "").trim();

export interface AdapterEnvelope {
  trace_id: string;
}

export interface AdapterResult<T> {
  trace_id: string;
  result?: T;
  error?: string;
}

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { "content-type": "application/json" };
  if (ADAPTER_TOKEN) h["authorization"] = `Bearer ${ADAPTER_TOKEN}`;
  return h;
}

export async function adapterPost<TIn extends AdapterEnvelope, TOut>(
  path: string,
  body: TIn,
): Promise<AdapterResult<TOut>> {
  const url = `${ADAPTER_BASE}${path}`;
  const started = Date.now();
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(body),
    });
    const latency_ms = Date.now() - started;
    if (!r.ok) {
      return {
        trace_id: body.trace_id,
        error: `adapter ${r.status}: ${await r.text()}`,
      };
    }
    const data = (await r.json()) as AdapterResult<TOut>;
    // Light request-latency log for the cost-router instrumentation.
    if (process.env.OPENCLAW_LOG_HTTP === "1") {
      console.log(`[adapter] ${path} ${r.status} ${latency_ms}ms trace=${body.trace_id}`);
    }
    return data;
  } catch (e) {
    return {
      trace_id: body.trace_id,
      error: `adapter fetch failed: ${(e as Error).message}`,
    };
  }
}

export async function adapterGet<TOut>(path: string, query?: Record<string, string | number>):
  Promise<AdapterResult<TOut>> {
  const q = query
    ? "?" + new URLSearchParams(
        Object.entries(query).map(([k, v]) => [k, String(v)] as [string, string])
      ).toString()
    : "";
  const url = `${ADAPTER_BASE}${path}${q}`;
  try {
    const r = await fetch(url, { headers: authHeaders() });
    if (!r.ok) {
      return { trace_id: "n/a", error: `adapter ${r.status}: ${await r.text()}` };
    }
    return (await r.json()) as AdapterResult<TOut>;
  } catch (e) {
    return { trace_id: "n/a", error: `adapter fetch failed: ${(e as Error).message}` };
  }
}

/** Generate a trace ID if the caller didn't pass one. */
export function newTraceId(prefix = "miya"): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
