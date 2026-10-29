/*
 * DclawOS routing decision (GPL-2.0-or-later; DclawOS-written code).
 *
 * Decides which provider/model serves a request, from the stripped
 * OmniRoute combos CONFIG/SCORING subset (see ../README.md). It does NOT
 * execute requests and does NOT call any gateway: the gateway dispatch
 * core (open-sse/services/combo.ts, handlers/chatCore.ts, executors) is
 * deliberately not vendored in Phase 2. The agent service dispatches
 * directly to providers using this ranking.
 *
 * Reuse notes (evaluation cards §4, step 0 retrieval):
 * - buildIntelligentProviderScores (intelligentRouting.ts) is ADAPTED:
 *   it normalizes weights and returns uniform baseline scores (1/N per
 *   candidate) with the weight factors attached. It is NOT a real ranker
 *   by itself — the 16-factor autoCombo scoring lives in the gateway
 *   (open-sse/services/autoCombo/, not vendored). This decision function
 *   therefore documents its basis honestly as "uniform-baseline" and
 *   leaves live cost/latency refinement to the agent service.
 * - Provider/model data comes from the registry (providerRegistry.ts:
 *   getRegisteredProviders, generateModels, generateAliasMap).
 * - The output shape reuses the smartRouting explanation STRUCTURE
 *   (routing_explanation string); it does NOT reuse smartRouting.ts's
 *   HTTP call to the gateway, and it does NOT fabricate a cost_envelope
 *   (no request has been executed at decision time, so no cost exists).
 * // reuse-check: open-sse/services/combo.ts resolveComboTargets NOT
 *   adopted, reason: it is gateway dispatch (needs handlers, executors,
 *   translators); Phase 2 vendors config/scoring only, per lightweight
 *   requirement. Decision ranking here is DclawOS-written on top of the
 *   subset helpers.
 * // reuse-check: src/lib/a2a/skills/smartRouting.ts executeSmartRouting
 *   NOT adopted, reason: it POSTs to the gateway over HTTP; only its
 *   explanation output shape is reused, the call is replaced by local
 *   scoring.
 */

import { buildIntelligentProviderScores } from "../src/lib/combos/intelligentRouting";
import {
  generateAliasMap,
  generateModels,
  getRegisteredProviders,
} from "../open-sse/config/providerRegistry";

export interface RoutingCandidate {
  provider: string;
  model: string;
  score: number;
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

export function decideRouting(input: {
  strategy?: string;
  provider?: string;
  model?: string;
}): RoutingDecision {
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

  const scores = buildIntelligentProviderScores({
    config: { candidatePool: pool },
  });
  const ranked: RoutingCandidate[] = scores.map((s) => ({
    provider: s.provider,
    model: "auto",
    score: s.score,
  }));
  // Stable sort: equal (uniform baseline) scores keep registry order.
  ranked.sort((a, b) => b.score - a.score);
  const chosen = ranked[0];
  if (!chosen) {
    throw new Error("empty provider pool");
  }
  const model = modelPin ?? "auto";
  const routing_explanation =
    `Strategy ${strategy}: ${pool.length} provider(s) in pool; ` +
    (providerPin !== null
      ? `pinned to provider ${providerPin}; `
      : "no provider pin; ") +
    (modelPin !== null
      ? `model pinned to ${modelPin}; `
      : "model auto (agent service resolves a default at dispatch); ") +
    `baseline uniform scores (1/N) from buildIntelligentProviderScores; ` +
    `chose ${chosen.provider}/${model}. ` +
    `Live cost/latency refinement is the agent service's job ` +
    `(gateway autoCombo scoring not vendored).`;
  return {
    strategy,
    provider: chosen.provider,
    model,
    ranked,
    routing_explanation,
    basis: "uniform-baseline",
    providerPin,
    modelPin,
  };
}
