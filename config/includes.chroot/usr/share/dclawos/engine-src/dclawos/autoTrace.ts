/*
 * DclawOS auto-evaluation trace sink (GPL-2.0-or-later; DclawOS-written code).
 *
 * // reuse-check: recordAutoExclusion (open-sse/services/autoCombo/
 *   autoEvaluationTrace.ts) NOT adopted: it funnels into combo/decisionTrace
 *   (the gateway invocation-trace layer, a Stage D item). Upstream tracing
 *   is best-effort and in-memory — recording an exclusion never changes a
 *   filtering decision — so Stage A keeps the call sites and drops the trace
 *   here. The agent service may install a real sink with setAutoTraceSink;
 *   until then every call is a no-op and routing is unaffected.
 */

export type AutoTraceSink = (
  invocationId: string | undefined,
  candidate: unknown,
  stage: string,
  reason: string,
  detail?: unknown
) => void;

let sink: AutoTraceSink | null = null;

export function setAutoTraceSink(next: AutoTraceSink | null): void {
  sink = next;
}

export function recordAutoExclusion(
  invocationId: string | undefined,
  candidate: unknown,
  stage: string,
  reason: string,
  detail?: unknown
): void {
  if (sink === null) return;
  try {
    sink(invocationId, candidate, stage, reason, detail);
  } catch {
    // Tracing must never break routing.
  }
}
