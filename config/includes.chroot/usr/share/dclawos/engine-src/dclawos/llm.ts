/*
 * DclawOS LLM execution path (GPL-2.0-or-later; DclawOS-written code).
 * Phase 4 module 1 (gap doc rows A1/A3/B4).
 *
 * This is the missing call path: dclawos-route DECIDES, this module
 * EXECUTES. Flow: decideRouting (16-factor ranking, decision.ts) ->
 * dispatch() attempt loop (dispatch.ts, stage 6 now wired to a real
 * executor) -> per-provider HTTP executor -> cost accounting from the
 * REAL response usage -> cost_envelope on the result.
 *
 * Executor shape follows upstream OmniRoute's DefaultExecutor
 * (open-sse/executors/default.ts `execute`): resolve the registry
 * entry, normalise the endpoint URL (registry baseUrl / urlBuilder /
 * urlSuffix), set the registry auth header (+ provider headers),
 * POST the OpenAI-shaped body, extract usage from the response.
 * // reuse-check: DefaultExecutor class NOT copied verbatim, reason:
 *   it is bound to the gateway's credential store, proxy pool and
 *   streaming/SSE machinery; the DclawOS executor keeps only the
 *   registry-driven request construction (the portable part) and is
 *   non-streaming by design (the agent loop consumes whole turns).
 * // reuse-check: gateway cost path traced, not copied: upstream
 *   computes per-request cost in src/lib/usage/costCalculator.ts
 *   (calculateCost/calculateCostDetailed) from response usage x DB
 *   pricing, invoked from handlers/chatCore usage stats; the result
 *   surfaces as cost_envelope in src/lib/a2a/skills/smartRouting.ts.
 *   Here pricing comes from the vendored registry pricing source
 *   (providerCostData.getModelPricing) — the same computation with
 *   the local pricing table instead of the gateway DB lookup.
 *
 * Key handling: API keys arrive in the request object from the
 * dclawos-llm CLI, which reads them from the process environment the
 * agent service sets from its 0600 secret files. Keys are never
 * logged, never echoed in results, never written anywhere here.
 *
 * Format scope: providers whose registry format is OpenAI-shaped
 * (the large majority of the catalog) execute directly. Registry
 * format "claude" (Anthropic Messages) is handled by the translator
 * branch in llmClaude.ts (Phase 4 module 1b); any other format fails
 * closed with an explicit error instead of a guessed request.
 */

import { dispatch, type DispatchTarget } from "./dispatch";
import { decideRouting } from "./decision";
import { getRegistryEntry } from "../open-sse/config/providerRegistry";
import { getModelPricing } from "../open-sse/services/providerCostData";
import {
  isModelLocked,
  recordModelLockoutFailure,
  recordProviderFailure,
  recordProviderSuccess,
  classifyLockoutReason,
} from "../open-sse/services/accountFallback";

// Phase 4 module 3 (gap row B1): the full accountFallback transplant
// is wired into the executor. DclawOS holds ONE credential per
// provider, so the provider id doubles as the connection id (the
// upstream connection scope collapses to the credential scope).
// - Before an attempt: a model locked by accountFallback is skipped
//   without a network call (reported as a durable 429 attempt).
// - On failure: recordProviderFailure feeds the provider breaker;
//   429/quota failures also record a model lockout failure with
//   upstream's classifyLockoutReason.
// - On success: recordProviderSuccess resets the breaker/cooldown
//   failure count (upstream semantics).
const FALLBACK_LOCKOUT_COOLDOWN_MS = 60_000;

function connectionIdFor(provider: string): string {
  return provider;
}

export interface LlmMessage {
  role: string;
  content?: unknown;
  tool_calls?: unknown;
  tool_call_id?: string;
  name?: string;
}

export interface LlmRequest {
  messages: LlmMessage[];
  /** Explicit target list; when absent, decideRouting ranks and the
   * top `maxTargets` providers WITH keys serve as targets. */
  targets?: DispatchTarget[];
  provider?: string;
  model?: string;
  taskType?: string;
  tools?: unknown[];
  maxTokens?: number;
  maxTargets?: number;
  /** Per-provider API keys (provider id -> key). Never logged. */
  keys: Record<string, string>;
  /** Per-provider endpoint override (custom/self-hosted endpoints;
   * mirrors upstream providerSpecificData.baseUrl). */
  baseUrlOverrides?: Record<string, string>;
  // Phase 6 M2: routing parameters assembled by llmlib from the
  // registry model_config; forwarded to decideRouting verbatim
  // (DecideRoutingInput already supports every field — no new
  // decision logic is added here).
  // reuse-check: field set ADOPTED from decision.ts
  //   DecideRoutingInput; this interface only carries them across.
  strategy?: string;
  routerStrategy?: string;
  modePack?: string;
  weights?: Record<string, number>;
  budgetCap?: number;
  budgetFallback?: "cheapest" | "strict";
  explorationRate?: number;
  sla?: {
    targetP95Ms?: number;
    maxErrorRate?: number;
    maxCostPer1MTokens?: number;
    hardConstraints?: boolean;
  };
  lastKnownGoodProvider?: string;
  /** Custom-provider / added-model pricing from the registry,
   * keyed "<provider>::<model>"; consulted by computeCost ahead
   * of the vendored pricing table (M2). */
  customPricing?: Record<
    string,
    { inputPer1M?: number | null; outputPer1M?: number | null }
  >;
}

export interface CostEnvelope {
  estimated: number;
  actual: number;
  currency: "USD";
  priced: boolean;
  usage: { promptTokens: number; completionTokens: number } | null;
}

export interface LlmResult {
  /** The serving provider's real response body, or null. */
  response: unknown;
  provider: string | null;
  model: string | null;
  attempts: Array<{
    provider: string;
    model: string;
    status: number | null;
    failureKind: string | null;
    error?: string;
  }>;
  fallbackCount: number;
  /** Real cost in USD from response usage x registry pricing, or
   * null when no request executed (no response => no cost exists). */
  costUsd: number | null;
  cost_envelope: CostEnvelope | null;
}

interface RegistryEntryLike {
  format?: string;
  baseUrl?: string;
  baseUrls?: string[];
  urlSuffix?: string;
  urlBuilder?: (base: string, model: string, stream: boolean) => string;
  authHeader?: string;
  authPrefix?: string;
  headers?: Record<string, string>;
  extraHeaders?: Record<string, string>;
}

function resolveEndpoint(
  entry: RegistryEntryLike,
  model: string,
  override: string | undefined
): string | null {
  if (override) return override;
  const base = entry.baseUrl ?? entry.baseUrls?.[0];
  if (!base) return null;
  let url = entry.urlBuilder ? entry.urlBuilder(base, model, false) : base;
  if (entry.urlSuffix && !url.includes("?")) url += entry.urlSuffix;
  return url;
}

function buildHeaders(
  entry: RegistryEntryLike,
  key: string | undefined
): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(entry.headers ?? {}),
    ...(entry.extraHeaders ?? {}),
  };
  if (key && entry.authHeader) {
    const prefix =
      entry.authPrefix ??
      (entry.authHeader === "Authorization" ? "Bearer " : "");
    headers[entry.authHeader] = prefix + key;
  } else if (key && !entry.authHeader) {
    headers["Authorization"] = `Bearer ${key}`;
  }
  return headers;
}

export function computeCost(
  provider: string,
  model: string,
  usage: { prompt_tokens?: number; completion_tokens?: number } | undefined,
  customPricing?: LlmRequest["customPricing"]
): { costUsd: number; priced: boolean } | null {
  if (!usage) return null;
  const prompt = Number(usage.prompt_tokens ?? 0);
  const completion = Number(usage.completion_tokens ?? 0);
  if (!Number.isFinite(prompt) || !Number.isFinite(completion)) return null;
  // Phase 6 M2: registry custom pricing wins when both rates are
  // present (a custom provider has no vendored pricing row; a
  // partial pricing falls through to the table rather than
  // silently zero-pricing the missing side).
  const custom = customPricing?.[`${provider}::${model}`];
  if (
    custom &&
    typeof custom.inputPer1M === "number" &&
    typeof custom.outputPer1M === "number"
  ) {
    const customCost =
      (prompt / 1_000_000) * custom.inputPer1M +
      (completion / 1_000_000) * custom.outputPer1M;
    return {
      costUsd: Math.round(customCost * 1_000_000) / 1_000_000,
      priced: true,
    };
  }
  const pricing = getModelPricing(provider, model);
  const cost =
    (prompt / 1_000_000) * pricing.inputCostPer1M +
    (completion / 1_000_000) * pricing.outputCostPer1M;
  return { costUsd: Math.round(cost * 1_000_000) / 1_000_000, priced: !pricing.isEstimated };
}

async function executeTarget(
  request: LlmRequest,
  target: DispatchTarget,
  signal: AbortSignal | undefined
): Promise<{ ok: boolean; status: number; body: unknown }> {
  const connId = connectionIdFor(target.provider);
  if (isModelLocked(target.provider, connId, target.model)) {
    return {
      ok: false,
      status: 429,
      body: { error: "model locked by accountFallback (cooldown)" },
    };
  }
  const startedAt = Date.now();
  const outcome = await executeTargetInner(request, target, signal);
  recordQuality(target.provider, target.model, outcome.ok, outcome.status, Date.now() - startedAt);
  if (outcome.ok) {
    recordProviderSuccess(target.provider, connId);
  } else {
    recordProviderFailure(target.provider, undefined, connId);
    if (outcome.status === 429 || outcome.status === 402 ||
        outcome.status === 403) {
      recordModelLockoutFailure(
        target.provider, connId, target.model,
        classifyLockoutReason(outcome.status), outcome.status,
        FALLBACK_LOCKOUT_COOLDOWN_MS);
    }
  }
  return outcome;
}

async function executeTargetInner(
  request: LlmRequest,
  target: DispatchTarget,
  signal: AbortSignal | undefined
): Promise<{ ok: boolean; status: number; body: unknown }> {
  const entry = (getRegistryEntry(target.provider) ?? {}) as RegistryEntryLike;
  const format = (entry.format ?? "openai").toLowerCase();
  if (format === "claude") {
    // Lazy import: the Anthropic translator branch (module 1b) must
    // not weigh down the OpenAI-shaped path.
    const claude = await import("./llmClaude");
    return claude.executeClaudeTarget(request, target, entry, signal);
  }
  if (format !== "openai" && format !== "passthrough") {
    return {
      ok: false,
      status: 0,
      body: { error: `unsupported registry format: ${format}` },
    };
  }
  const url = resolveEndpoint(
    entry,
    target.model,
    request.baseUrlOverrides?.[target.provider]
  );
  if (!url) {
    return { ok: false, status: 0, body: { error: `no endpoint for provider ${target.provider}` } };
  }
  const body: Record<string, unknown> = {
    model: target.model,
    messages: request.messages,
    stream: false,
  };
  if (request.tools && request.tools.length > 0) {
    body.tools = request.tools;
    body.tool_choice = "auto";
  }
  if (typeof request.maxTokens === "number") body.max_tokens = request.maxTokens;
  const res = await fetch(url, {
    method: "POST",
    headers: buildHeaders(entry, request.keys[target.provider]),
    body: JSON.stringify(body),
    signal,
  });
  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  return { ok: res.ok, status: res.status, body: parsed };
}

export async function runLlm(request: LlmRequest): Promise<LlmResult> {
  let targets = request.targets ?? [];
  if (targets.length === 0) {
    const decision = decideRouting({
      provider: request.provider,
      model: request.model,
      taskType: request.taskType,
      promptMessages: request.messages.map((m) => ({
        role: m.role,
        content: m.content,
      })),
      // Phase 6 M2 routing pass-through (see LlmRequest).
      strategy: request.strategy,
      routerStrategy: request.routerStrategy,
      modePack: request.modePack,
      weights: request.weights,
      budgetCap: request.budgetCap,
      budgetFallback: request.budgetFallback,
      explorationRate: request.explorationRate,
      sla: request.sla,
      lastKnownGoodProvider: request.lastKnownGoodProvider,
    });
    const maxTargets = request.maxTargets ?? 3;
    const seen = new Set<string>();
    for (const cand of decision.ranked) {
      if (targets.length >= maxTargets) break;
      if (seen.has(cand.provider)) continue;
      seen.add(cand.provider);
      // Only providers we actually hold a key for can serve; keyless
      // ranked providers are skipped, never attempted blind.
      if (!(cand.provider in request.keys)) continue;
      targets.push({
        provider: cand.provider,
        model:
          request.provider === cand.provider && request.model
            ? request.model
            : cand.model,
      });
    }
    if (targets.length === 0 && request.provider && request.model) {
      targets.push({ provider: request.provider, model: request.model });
    }
  }
  if (targets.length === 0) {
    return {
      response: null,
      provider: null,
      model: null,
      attempts: [],
      fallbackCount: 0,
      costUsd: null,
      cost_envelope: null,
    };
  }
  const result = await dispatch(
    { targets, taskType: request.taskType },
    (target, signal) => executeTarget(request, target, signal)
  );
  const served =
    result.response !== null
      ? result.attempts.find((a) => a.failureKind === null)?.target ??
        targets[result.fallbackCount] ??
        null
      : null;
  let costUsd: number | null = null;
  let envelope: CostEnvelope | null = null;
  if (result.response && served) {
    const body = result.response as {
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const computed = computeCost(
      served.provider,
      served.model,
      body.usage,
      request.customPricing
    );
    if (computed) {
      costUsd = computed.costUsd;
      envelope = {
        estimated: computed.costUsd,
        actual: computed.costUsd,
        currency: "USD",
        priced: computed.priced,
        usage: {
          promptTokens: Number(body.usage?.prompt_tokens ?? 0),
          completionTokens: Number(body.usage?.completion_tokens ?? 0),
        },
      };
    }
  }
  return {
    response: result.response,
    provider: served?.provider ?? null,
    model: served?.model ?? null,
    attempts: result.attempts.map((a) => ({
      provider: a.target.provider,
      model: a.target.model,
      status: a.status,
      failureKind: a.failureKind,
      ...(a.error ? { error: a.error } : {}),
    })),
    fallbackCount: result.fallbackCount,
    costUsd,
    cost_envelope: envelope,
  };
}

export const DCLAWOS_LLM_MARKER = "dclawos-llm-executor-v1";

// Re-exported for the dclawos-llm --selftest-fallback gate: the
// accountFallback surface the executor is wired to (module 3).
export {
  isModelLocked,
  lockModel,
  clearModelLock,
  getModelLockoutInfo,
  isProviderInCooldown,
  getProviderBreakerState,
  classifyLockoutReason,
  isAccountDeactivated,
  isCreditsExhausted,
} from "../open-sse/services/accountFallback";
export {
  setConnectionRateLimitUntil,
  getConnectionRateLimitUntil,
} from "../open-sse/services/dbAdapter";
// Re-exported for the dclawos-llm --selftest-quality gate
// (module 4): persisted provider quality + the chaos gate.
export {
  recordQuality,
  qualityOf,
  chaosEnabled,
  dispatchChaosFromCombo,
} from "./qualityStore";
import { recordQuality } from "./qualityStore";
// Phase 6 M2: re-exported for the dclawos-llm --status inspect mode
// (read-only; the settings host consumes its JSON). getQualitySnapshot
// is upstream routing/quality via qualityStore's import surface;
// getAllModelLockouts is accountFallback's in-process lockout list —
// a fresh --status process honestly reports an empty list (see
// docs/PHASE6.md M2 ledger for the cross-process limitation).
export { getQualitySnapshot } from "../open-sse/services/routing/quality";
export { getAllModelLockouts } from "../open-sse/services/accountFallback";
