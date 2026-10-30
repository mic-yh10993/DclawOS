/*
 * DclawOS dispatch skeleton (GPL-2.0-or-later; DclawOS-written code).
 *
 * Stage D (§3.5): the LOCAL dispatcher the agent service will call later.
 * This module is intentionally NOT imported by dclawos/decision.ts — the
 * decision bundle must stay a fast, offline, synchronously importable
 * module, while dispatch is async, stateful, and lazily loaded. (Verified by
 * the build check: decision.js must not contain the "dclawos-dispatch"
 * marker below.)
 *
 * Pipeline stages, named after the upstream distribution core (§1b) so the
 * later full port has a place to land each one:
 *   1. expandTargets        — comboStructure.resolveComboTargets (NOT ported:
 *                              nested-combo expansion + cycle guard; the
 *                              skeleton takes an already-ordered target list)
 *   2. setupDispatch        — dispatchPrelude.phaseComboSetup (strategy /
 *                              session / timeout resolution, plain args)
 *   3. scoreTargets         — autoStrategy.scoreAutoTargets via scorePool
 *                              (REAL 16-factor scoring, shared pool maxima)
 *   4. orderTargets         — attempt-loop order: durable exhaustion
 *                              (quota/credits/auth) survives set retries,
 *                              transient failures replay (comboAttemptLoop)
 *   5. executeWithRetry     — per-target attempt + transient-backend retry
 *                              (502/503/504 only, decorrelated jitter) and
 *                              failure classification into durable/transient
 *   6. translate + execute   — NOT implemented here: translator/* and
 *                              executors/* are lazily loaded by the agent
 *                              service per provider (see README). The caller
 *                              supplies executeTarget; this skeleton never
 *                              invents a provider response.
 *
 * Cost honesty: DispatchResult.costUsd is null unless the executor callback
 * returns real usage. A cost_envelope is computed by the agent service from
 * an ACTUAL upstream response (usage tokens × registry pricing) — never in
 * decideRouting, never fabricated here (analysis §3.8 item 2: the exact
 * gateway cost-computation function was not traced to a single symbol).
 */

import {
  scorePool,
  normalizeScoringWeights,
  type ProviderCandidate,
  type ScoredProvider,
  type ScoringWeights,
} from "../open-sse/services/autoCombo/scoring";
import { getTaskFitness } from "../open-sse/services/autoCombo/taskFitness";
import {
  isResponseStatusRetryable,
  runWithTransientBackendRetry,
  type ResponseLike,
} from "../open-sse/services/transientBackendRetry";

export interface DispatchTarget {
  provider: string;
  model: string;
  connectionId?: string;
}

export interface DispatchRequest {
  targets: DispatchTarget[];
  taskType?: string;
  weights?: Partial<ScoringWeights>;
  telemetry?: Partial<ProviderCandidate>[];
  maxSetRetries?: number;
  setRetryDelayMs?: number;
  signal?: AbortSignal;
}

export interface DispatchAttempt {
  target: DispatchTarget;
  status: number | null;
  failureKind: "durable" | "transient" | "aborted" | null;
  error?: string;
}

export interface DispatchResult {
  /** The executor's real response body, or null when every target failed. */
  response: unknown;
  attempts: DispatchAttempt[];
  /** Index of the serving target (upstream X-OmniRoute-Fallback-Attempts). */
  fallbackCount: number;
  /** Real cost in USD, or null: no request executed means no cost exists. */
  costUsd: number | null;
  /** Per-target scores from stage 3 (empty when scoring had no candidates). */
  scored: ScoredProvider[];
}

/**
 * Executor callback supplied by the agent service (lazy provider import).
 * Returns a ResponseLike whose body is the upstream response; usage/cost
 * accounting happens in the agent service from the real response, not here.
 */
export type ExecuteTarget = (
  target: DispatchTarget,
  signal: AbortSignal | undefined
) => Promise<ResponseLike & { body: unknown }>;

/** Stage 1 — target expansion placeholder: ordered list passes through. */
export function expandTargets(targets: DispatchTarget[]): DispatchTarget[] {
  return [...targets];
}

/** Stage 2 — dispatch prelude: strategy/session/timeout resolution (plain args). */
export function setupDispatch(request: DispatchRequest): {
  taskType: string;
  weights: ScoringWeights;
  maxSetRetries: number;
  setRetryDelayMs: number;
} {
  return {
    taskType: (request.taskType ?? "default").trim() || "default",
    weights: normalizeScoringWeights(request.weights ?? null),
    maxSetRetries: request.maxSetRetries ?? 0,
    setRetryDelayMs: request.setRetryDelayMs ?? 0,
  };
}

/** Stage 3 — real 16-factor scoring of the target list (shared pool maxima). */
export function scoreTargets(
  targets: DispatchTarget[],
  taskType: string,
  weights: ScoringWeights,
  telemetry?: Partial<ProviderCandidate>[]
): ScoredProvider[] {
  const byKey = new Map<string, Partial<ProviderCandidate>>();
  const byProvider = new Map<string, Partial<ProviderCandidate>>();
  for (const t of telemetry ?? []) {
    if (typeof t.provider !== "string") continue;
    if (typeof t.model === "string") byKey.set(`${t.provider}::${t.model}`, t);
    else if (!byProvider.has(t.provider)) byProvider.set(t.provider, t);
  }
  const pool: ProviderCandidate[] = targets.map((target) => {
    const tel =
      byKey.get(`${target.provider}::${target.model}`) ??
      byProvider.get(target.provider) ??
      {};
    return {
      provider: target.provider,
      model: target.model,
      quotaRemaining: typeof tel.quotaRemaining === "number" ? tel.quotaRemaining : 100,
      quotaTotal: typeof tel.quotaTotal === "number" ? tel.quotaTotal : 0,
      circuitBreakerState: tel.circuitBreakerState ?? "CLOSED",
      costPer1MTokens: typeof tel.costPer1MTokens === "number" ? tel.costPer1MTokens : 5,
      p95LatencyMs: typeof tel.p95LatencyMs === "number" ? tel.p95LatencyMs : 1000,
      latencyStdDev: typeof tel.latencyStdDev === "number" ? tel.latencyStdDev : 0,
      errorRate: typeof tel.errorRate === "number" ? tel.errorRate : 0,
      connectionId: target.connectionId ?? tel.connectionId,
    };
  });
  if (pool.length === 0) return [];
  return scorePool(pool, taskType, weights, getTaskFitness);
}

/**
 * Stage 5 — failure classification (upstream policy table, §1b.3, without
 * accountFallback's per-provider classifiers, which are a Stage D follow-up):
 * quota/credits/auth failures are DURABLE (they survive set retries);
 * 502/503/504 and network errors are TRANSIENT (replayed); 429 is treated
 * as DURABLE here because upstream uses it as a per-tenant policy signal,
 * not as a retryable transient (transientBackendRetry deliberately excludes
 * 429 from retryable statuses).
 */
export function classifyFailure(status: number | null, errorText?: string): "durable" | "transient" {
  if (status === null) return "transient";
  if (status === 429) return "durable";
  if (status === 401 || status === 403) return "durable";
  if (status === 402) return "durable";
  if (isResponseStatusRetryable(status)) return "transient";
  if (/quota|credit|exhausted|insufficient/i.test(errorText ?? "")) return "durable";
  return "transient";
}

/** Stage 4+5 — ordered attempt loop with set retries and transient retry. */
export async function dispatch(
  request: DispatchRequest,
  executeTarget: ExecuteTarget
): Promise<DispatchResult> {
  const setup = setupDispatch(request);
  const ordered = expandTargets(request.targets);
  const scored = scoreTargets(ordered, setup.taskType, setup.weights, request.telemetry);
  // Attempt order follows scored order; unscored targets (none here, since
  // scoreTargets scores every target) would append in input order.
  const scoreOrder = new Map(scored.map((s, i) => [`${s.provider}::${s.model}`, i]));
  const attemptOrder = [...ordered].sort(
    (a, b) =>
      (scoreOrder.get(`${a.provider}::${a.model}`) ?? 1e9) -
      (scoreOrder.get(`${b.provider}::${b.model}`) ?? 1e9)
  );

  const attempts: DispatchAttempt[] = [];
  const durableFailed = new Set<string>();
  const maxSets = setup.maxSetRetries + 1;
  for (let set = 0; set < maxSets; set++) {
    for (let index = 0; index < attemptOrder.length; index++) {
      const target = attemptOrder[index];
      const key = `${target.provider}::${target.model}`;
      if (durableFailed.has(key)) continue;
      if (request.signal?.aborted) {
        attempts.push({ target, status: null, failureKind: "aborted", error: "aborted" });
        return { response: null, attempts, fallbackCount: index, costUsd: null, scored };
      }
      try {
        const outcome = await runWithTransientBackendRetry(
          () => executeTarget(target, request.signal),
          { signal: request.signal }
        );
        const status = outcome.status;
        if (outcome.ok) {
          attempts.push({ target, status, failureKind: null });
          return {
            response: outcome.body,
            attempts,
            fallbackCount: index,
            costUsd: null,
            scored,
          };
        }
        const kind = classifyFailure(status);
        attempts.push({ target, status, failureKind: kind });
        if (kind === "durable") durableFailed.add(key);
      } catch (error) {
        if (request.signal?.aborted) {
          attempts.push({
            target,
            status: null,
            failureKind: "aborted",
            error: error instanceof Error ? error.message : String(error),
          });
          return { response: null, attempts, fallbackCount: index, costUsd: null, scored };
        }
        const kind = classifyFailure(null, error instanceof Error ? error.message : String(error));
        attempts.push({
          target,
          status: null,
          failureKind: kind,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    if (set + 1 < maxSets && setup.setRetryDelayMs > 0) {
      await new Promise<void>((resolve) => setTimeout(resolve, setup.setRetryDelayMs));
    }
  }
  return { response: null, attempts, fallbackCount: attemptOrder.length, costUsd: null, scored };
}

export const DCLAWOS_DISPATCH_MARKER = "dclawos-dispatch-skeleton-v1";
