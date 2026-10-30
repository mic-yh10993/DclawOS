/*
 * DclawOS routing decision (GPL-2.0-or-later; DclawOS-written code).
 *
 * Decides which provider/model serves a request, from the vendored OmniRoute
 * autoCombo scoring engine (see ../README.md). It does NOT execute requests
 * and does NOT call any gateway: dispatch lives in dclawos/dispatch.ts (a
 * lazily loaded skeleton the agent service calls later), not in this
 * synchronous, offline decision bundle.
 *
 * Phase 3 module 3 (real scoring) replaces the Phase 2 uniform baseline:
 * candidates are scored with the real 16-factor scorePool (scoring.ts) and
 * the winner follows the requested RouterStrategy (routerStrategy.ts).
 * Signatures and return shapes are preserved: decideRouting / buildCatalog
 * keep their exact fields; every new input is optional and every new output
 * is an additive field. basis is now "autocombo-16-factor".
 *
 * Reuse notes (evaluation cards §4, step 0 retrieval):
 * - scorePool / calculateFactors / DEFAULT_WEIGHTS (scoring.ts): ADOPTED
 *   verbatim. Pool maxima are computed once per pool (computePoolMaxima
 *   inside scorePool) — never per candidate.
 * - selectProvider (engine.ts): ADOPTED only when the caller explicitly
 *   requests exploration (explorationRate > 0). Its ScoreTierRotator uses
 *   Math.random, so the default CLI path (explorationRate 0) must stay
 *   deterministic: ranked order is pure scorePool order and the winner is
 *   the strategy pick over that order.
 * - getModePack (modePacks.ts): ADOPTED. An unknown mode pack falls back
 *   to caller weights / DEFAULT_WEIGHTS without throwing; the output
 *   modePack field is then null (fallback is visible, not silent).
 * - selectWithStrategy (routerStrategy.ts): ADOPTED for the winner pick.
 *   "nadir" is not registered (external HTTP API, offline path) and falls
 *   back to "rules" with upstream's unknown-strategy warning.
 * - getTaskFitness (taskFitness.ts): ADOPTED with the §3.2 JSON adaptation
 *   (SQLite model_intelligence -> fitness-*.json files; see that file's
 *   reuse-check comments). Intent classification mirrors engine.ts:
 *   taskType "default" + prompt messages -> classifyPromptIntent bridged by
 *   intentTaskFitnessMap; short prompts (<=10 chars) -> "simple".
 * - getModelPricing (providerCostData.ts): ADOPTED for neutral candidate
 *   costs when the caller supplies no telemetry. These are REAL registry
 *   prices, not invented numbers; a pricing lookup failure falls back to
 *   upstream's generic estimate (5.0 input per 1M) exactly as
 *   getModelPricing itself does.
 * - No cost_envelope is emitted: no request has been executed at decision
 *   time, so no cost exists to report (fabricating one would be dishonest;
 *   cost materializes at execution time in the agent service).
 * // reuse-check: buildIntelligentProviderScores (intelligentRouting.ts)
 *   NOT used anymore, reason: it returns uniform baseline scores (1/N) —
 *   Phase 2's honest placeholder. Phase 3 replaces that call path with
 *   scorePool/selectProvider; the uniform function is not extended.
 * // reuse-check: open-sse/services/combo.ts resolveComboTargets NOT
 *   adopted, reason: gateway dispatch (needs handlers, executors,
 *   translators); see dclawos/dispatch.ts for the local skeleton.
 */

import {
  scorePool,
  DEFAULT_WEIGHTS,
  normalizeScoringWeights,
  validateWeights,
  type ProviderCandidate,
  type ScoringFactors,
  type ScoringWeights,
  type ScoredProvider,
} from "../open-sse/services/autoCombo/scoring";
import { getTaskFitness } from "../open-sse/services/autoCombo/taskFitness";
import { getModePack, getModePackNames } from "../open-sse/services/autoCombo/modePacks";
import {
  selectProvider,
  BudgetExceededError,
} from "../open-sse/services/autoCombo/engine";
import {
  listStrategies,
  selectWithStrategy,
  type SlaRoutingPolicy,
} from "../open-sse/services/autoCombo/routerStrategy";
import { classifyPromptIntent } from "../open-sse/services/intentClassifier";
import { mapIntentToTaskFitnessKey } from "../open-sse/services/autoCombo/intentTaskFitnessMap";
import { getModelPricing } from "../open-sse/services/providerCostData";
import {
  generateAliasMap,
  generateModels,
  getRegisteredProviders,
} from "../open-sse/config/providerRegistry";

// Re-exported for the dclawos-route --selftest gate (additive exports only;
// decideRouting / buildCatalog signatures are unchanged).
export { DEFAULT_WEIGHTS, validateWeights, normalizeScoringWeights };
export { getModePack, getModePackNames };
export { listStrategies };
export { BudgetExceededError };

export interface RoutingCandidate {
  provider: string;
  model: string;
  score: number;
  /** Factor detail per candidate (additive). */
  factors?: ScoringFactors;
}

export interface RoutingDecision {
  strategy: string;
  provider: string;
  model: string;
  ranked: RoutingCandidate[];
  routing_explanation: string;
  basis: string;
  providerPin: string | null;
  modelPin: string | null;
  /** Winning candidate's 16 factors (additive). */
  factors?: ScoringFactors;
  /** True only when the caller requested exploration and it fired. */
  isExploration?: boolean;
  /** Providers excluded by self-healing / OPEN-breaker filtering (additive). */
  excluded?: string[];
  /** Effective mode pack, or null when none applied / unknown name fell back. */
  modePack?: string | null;
}

export interface CatalogModel {
  id: string;
  name: string | null;
}

export interface CatalogProvider {
  id: string;
  alias: string | null;
  models: CatalogModel[];
}

/** Caller-supplied telemetry for one provider/model (all fields optional). */
export interface DecisionTelemetry {
  provider: string;
  model?: string;
  quotaRemaining?: number;
  quotaTotal?: number;
  circuitBreakerState?: "CLOSED" | "HALF_OPEN" | "OPEN";
  costPer1MTokens?: number;
  p95LatencyMs?: number;
  latencyStdDev?: number;
  errorRate?: number;
  failureRate?: number;
  accountTier?: "ultra" | "pro" | "standard" | "free";
  quotaResetIntervalSecs?: number;
  contextAffinity?: number;
  cacheAffinity?: number;
  sessionAvailability?: number;
  resetWindowAffinity?: number;
  connectionPoolSize?: number;
  connectionId?: string;
  quality?: number;
  avgTtftMs?: number;
  avgE2ELatencyMs?: number;
  avgTokensPerSecond?: number;
}

export interface DecideRoutingInput {
  strategy?: string;
  provider?: string;
  model?: string;
  taskType?: string;
  promptMessages?: Array<{ role: string; content: unknown }>;
  weights?: Partial<ScoringWeights>;
  modePack?: string;
  routerStrategy?: string;
  telemetry?: DecisionTelemetry[];
  budgetCap?: number;
  budgetFallback?: "cheapest" | "strict";
  /** Default 0 for determinism (upstream selectProvider default is 0.05). */
  explorationRate?: number;
  estimatedInputTokens?: number;
  lastKnownGoodProvider?: string;
  sla?: SlaRoutingPolicy;
}

export function buildCatalog(): {
  source: string;
  providers: CatalogProvider[];
} {
  const modelsByKey = generateModels();
  const aliasMap = generateAliasMap();
  const providers = getRegisteredProviders().map((id) => {
    const alias: string | null =
      typeof aliasMap[id] === "string" ? aliasMap[id] : null;
    const list =
      modelsByKey[alias ?? id] ?? modelsByKey[id] ?? [];
    const models: CatalogModel[] = list
      .map((m) => {
        const rec = m as unknown as Record<string, unknown>;
        const mid = typeof rec.id === "string" ? rec.id : "";
        const name = typeof rec.name === "string" ? rec.name : null;
        return { id: mid, name };
      })
      .filter((m) => m.id.length > 0);
    return { id, alias, models };
  });
  return { source: "omniroute-3.8.53", providers };
}

function firstCatalogModel(provider: string): string {
  const modelsByKey = generateModels();
  const aliasMap = generateAliasMap();
  const alias = aliasMap[provider];
  const list = modelsByKey[alias ?? provider] ?? modelsByKey[provider] ?? [];
  const first = list[0] as unknown as Record<string, unknown> | undefined;
  return typeof first?.id === "string" && first.id.length > 0 ? first.id : "auto";
}

function finiteOr(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** Mirror of engine.ts intent classification (see header reuse notes). */
function resolveTaskType(
  taskType: string | undefined,
  promptMessages: DecideRoutingInput["promptMessages"]
): string {
  const base = (taskType ?? "default").trim() || "default";
  if (base !== "default" || !promptMessages?.length) return base;
  const lastUserMsg = [...promptMessages].reverse().find((m) => m.role === "user");
  if (!lastUserMsg) return base;
  const text =
    typeof lastUserMsg.content === "string"
      ? lastUserMsg.content
      : Array.isArray(lastUserMsg.content)
        ? (lastUserMsg.content as Array<{ type: string; text?: string }>)
            .filter((b) => b.type === "text")
            .map((b) => b.text || "")
            .join(" ")
        : "";
  if (text.length <= 10) return "simple";
  return mapIntentToTaskFitnessKey(classifyPromptIntent(text));
}

function buildCandidates(
  pool: string[],
  modelPin: string | null,
  telemetry: DecisionTelemetry[] | undefined
): ProviderCandidate[] {
  const byExact = new Map<string, DecisionTelemetry>();
  const byProvider = new Map<string, DecisionTelemetry>();
  for (const entry of telemetry ?? []) {
    if (typeof entry.provider !== "string") continue;
    if (typeof entry.model === "string" && entry.model.length > 0) {
      byExact.set(`${entry.provider}::${entry.model}`, entry);
    } else if (!byProvider.has(entry.provider)) {
      byProvider.set(entry.provider, entry);
    }
  }
  // One candidate per provider by default; a telemetry entry naming a model
  // adds that (provider, model) pair as its own candidate so per-model
  // telemetry is never silently collapsed into a provider average.
  const seen = new Set<string>();
  const candidates: ProviderCandidate[] = [];
  const pushCandidate = (provider: string, model: string, tel: DecisionTelemetry | undefined) => {
    const key = `${provider}::${model}`;
    if (seen.has(key)) return;
    seen.add(key);
    let cost = 5;
    try {
      cost = getModelPricing(provider, model).inputCostPer1M;
    } catch {
      cost = 5;
    }
    const breaker =
      tel?.circuitBreakerState === "OPEN" ||
      tel?.circuitBreakerState === "HALF_OPEN" ||
      tel?.circuitBreakerState === "CLOSED"
        ? tel.circuitBreakerState
        : "CLOSED";
    candidates.push({
      provider,
      model,
      quotaRemaining: finiteOr(tel?.quotaRemaining, 100),
      quotaTotal: finiteOr(tel?.quotaTotal, 0),
      circuitBreakerState: breaker,
      costPer1MTokens: finiteOr(tel?.costPer1MTokens, cost),
      p95LatencyMs: finiteOr(tel?.p95LatencyMs, 1000),
      latencyStdDev: finiteOr(tel?.latencyStdDev, 0),
      errorRate: finiteOr(tel?.errorRate, 0),
      failureRate: tel?.failureRate,
      accountTier: tel?.accountTier,
      quotaResetIntervalSecs: tel?.quotaResetIntervalSecs,
      contextAffinity: tel?.contextAffinity,
      cacheAffinity: tel?.cacheAffinity,
      sessionAvailability: tel?.sessionAvailability,
      resetWindowAffinity: tel?.resetWindowAffinity,
      connectionPoolSize: tel?.connectionPoolSize,
      connectionId: tel?.connectionId,
      quality: tel?.quality,
      avgTtftMs: tel?.avgTtftMs,
      avgE2ELatencyMs: tel?.avgE2ELatencyMs,
      avgTokensPerSecond: tel?.avgTokensPerSecond,
    });
  };
  for (const provider of pool) {
    const model = modelPin ?? firstCatalogModel(provider);
    const tel = byExact.get(`${provider}::${model}`) ?? byProvider.get(provider);
    pushCandidate(provider, model, tel);
  }
  // Telemetry pairs for models outside the default (first-model) choice.
  for (const entry of telemetry ?? []) {
    if (typeof entry.provider !== "string" || !pool.includes(entry.provider)) continue;
    if (typeof entry.model === "string" && entry.model.length > 0) {
      pushCandidate(entry.provider, entry.model, entry);
    }
  }
  return candidates;
}

export function decideRouting(input: DecideRoutingInput): RoutingDecision {
  const strategy = (input.strategy ?? "auto").trim() || "auto";
  const providers = getRegisteredProviders();
  const providerPin =
    typeof input.provider === "string" && input.provider.trim().length > 0
      ? input.provider.trim()
      : null;
  const modelPin =
    typeof input.model === "string" && input.model.trim().length > 0
      ? input.model.trim()
      : null;

  let pool = providers;
  if (providerPin !== null) {
    if (!providers.includes(providerPin)) {
      throw new Error(`unknown provider: ${providerPin}`);
    }
    pool = [providerPin];
  }

  const taskType = resolveTaskType(input.taskType, input.promptMessages);
  const modePackName =
    typeof input.modePack === "string" && input.modePack.trim().length > 0
      ? input.modePack.trim()
      : null;
  const pack = modePackName !== null ? getModePack(modePackName) : undefined;
  const weights =
    pack !== undefined
      ? normalizeScoringWeights(pack)
      : normalizeScoringWeights(input.weights ?? null);
  const routerStrategyName =
    typeof input.routerStrategy === "string" && input.routerStrategy.trim().length > 0
      ? input.routerStrategy.trim()
      : "rules";
  const explorationRate = Math.min(
    1,
    Math.max(0, finiteOr(input.explorationRate, 0))
  );

  const candidates = buildCandidates(pool, modelPin, input.telemetry);
  if (candidates.length === 0) {
    throw new Error("empty provider pool");
  }
  const scored: ScoredProvider[] = scorePool(candidates, taskType, weights, getTaskFitness);
  const ranked: RoutingCandidate[] = scored.map((s) => ({
    provider: s.provider,
    model: s.model,
    score: s.score,
    factors: s.factors,
  }));

  let winner: ScoredProvider = scored[0];
  let isExploration = false;
  let excluded: string[] = [];

  if (explorationRate > 0) {
    // Upstream selection semantics (tier rotator + bandit exploration) —
    // only when the caller explicitly opts into non-determinism.
    const selection = selectProvider(
      {
        id: "dclawos",
        name: "dclawos",
        type: "auto",
        candidatePool: pool,
        weights,
        modePack: pack !== undefined ? modePackName ?? undefined : undefined,
        budgetCap: input.budgetCap,
        budgetFallback: input.budgetFallback,
        explorationRate,
      },
      candidates,
      taskType,
      input.promptMessages
    );
    const match = scored.find(
      (s) => s.provider === selection.provider && s.model === selection.model
    );
    if (match) winner = match;
    isExploration = selection.isExploration;
    excluded = selection.excluded;
  } else {
    // Deterministic winner: the requested strategy over the scored pool.
    // OPEN-breaker candidates are filtered by the strategies themselves
    // (rules/score/cost/sla fall back to the full pool when all are OPEN),
    // mirroring upstream; excluded lists the filtered-out OPEN providers.
    const strategyDecision = selectWithStrategy(candidates, {
      taskType,
      weights,
      explorationRate: 0,
      lastKnownGoodProvider: input.lastKnownGoodProvider,
      sla: input.sla,
    }, routerStrategyName);
    const match = scored.find(
      (s) => s.provider === strategyDecision.provider && s.model === strategyDecision.model
    );
    if (match) winner = match;
    const anyHealthy = candidates.some((c) => c.circuitBreakerState !== "OPEN");
    excluded = anyHealthy
      ? candidates
          .filter((c) => c.circuitBreakerState === "OPEN")
          .map((c) => c.provider)
      : [];

    if (typeof input.budgetCap === "number" && Number.isFinite(input.budgetCap)) {
      // Deterministic budget enforcement (engine.ts semantics, score order):
      // estimated cost = costPer1MTokens / 1e6 * estimatedInputTokens.
      const estimatedTokens =
        Number.isFinite(input.estimatedInputTokens) && (input.estimatedInputTokens ?? 0) > 0
          ? (input.estimatedInputTokens as number)
          : 1000;
      const costByKey = new Map(
        candidates.map((c) => [`${c.provider}::${c.model}`, c.costPer1MTokens])
      );
      const estimatedCostFor = (s: ScoredProvider) =>
        ((costByKey.get(`${s.provider}::${s.model}`) ?? 0) / 1_000_000) * estimatedTokens;
      const withinCap =
        estimatedCostFor(winner) <= (input.budgetCap as number)
          ? winner
          : scored.find((s) => estimatedCostFor(s) <= (input.budgetCap as number));
      if (withinCap !== undefined) {
        winner = withinCap;
      } else {
        const cheapest = [...scored].sort(
          (a, b) => estimatedCostFor(a) - estimatedCostFor(b)
        )[0];
        if (input.budgetFallback === "strict") {
          throw new BudgetExceededError(
            input.budgetCap as number,
            cheapest ? estimatedCostFor(cheapest) : 0
          );
        }
        if (cheapest) winner = cheapest;
      }
    }
  }

  const model = modelPin ?? winner.model;
  const f = winner.factors;
  const routing_explanation =
    `Strategy ${strategy}: ${pool.length} provider(s) in pool; ` +
    (providerPin !== null
      ? `pinned to provider ${providerPin}; `
      : "no provider pin; ") +
    (modelPin !== null
      ? `model pinned to ${modelPin}; `
      : `model auto (winner candidate ${winner.provider}/${winner.model}); `) +
    `16-factor autocombo scoring (router strategy ${routerStrategyName}` +
    (pack !== undefined ? `, mode pack ${modePackName}` : "") +
    `); chose ${winner.provider}/${model} score=${winner.score.toFixed(3)} ` +
    `(quota=${f.quota.toFixed(2)}, health=${f.health.toFixed(2)}, ` +
    `costInv=${f.costInv.toFixed(2)}, taskFit=${f.taskFit.toFixed(2)}). ` +
    (excluded.length > 0 ? `Excluded: ${excluded.join(", ")}. ` : "") +
    `No request executed; no cost_envelope at decision time.`;
  return {
    strategy,
    provider: winner.provider,
    model,
    ranked,
    routing_explanation,
    basis: "autocombo-16-factor",
    providerPin,
    modelPin,
    factors: winner.factors,
    isExploration,
    excluded,
    modePack: pack !== undefined ? modePackName : null,
  };
}
