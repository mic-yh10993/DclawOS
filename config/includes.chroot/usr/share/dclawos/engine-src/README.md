# DclawOS routing engine source (Phase 2 module 4 + Phase 3 module 3)

## Source

Stripped subset of OmniRoute (MIT; upstream LICENSE preserved as
LICENSE.UPSTREAM-OMNIROUTE in this directory):
- Upstream: diegosouzapw/OmniRoute (canonical cheaper-inference/OmniRoute;
  the old name redirects), package version 3.8.53, shallow-clone HEAD
  5332fe88 at strip time.
- Phase 2 seeds: src/lib/combos/{intelligentRouting, steps, compositeTiers,
  snapshotWeights, controlCenter}.ts.
- Phase 3 module 3 adds the full autoCombo scoring engine (analysis:
  research/dclawos-reboot/phase3/omni-full-routing-analysis.md, stages
  A–D): open-sse/services/autoCombo/{scoring, engine, modePacks,
  taskFitness, scoresAs, intentTaskFitnessMap, routerStrategy,
  speedRanking, selfHealing, snapshotBreaker, resilienceCandidateFilter,
  providerDiversity, modelExposureFilter, modelLockoutFilter,
  failClosedPool, intelligenceLookupCache}.ts, plus
  open-sse/services/{intentClassifier, modelLifecycle, tierResolver,
  tierTypes, tierConfig, providerCostData, specificityTypes,
  specificityDetector, specificityRules, manifestAdapter,
  opencodeFreeTierSkip, connectionCircuitBreaker,
  transientBackendRetry}.ts, open-sse/utils/number.ts,
  open-sse/executors/codex/reasoningSuffix.ts, the pricing closure
  (src/shared/constants/pricing/**, src/shared/utils/{formatting,
  maskEmail, globPattern, payloadFreeEvidence, freeModels, classify429}.ts,
  src/shared/constants/models.ts, open-sse/config/{freeModelCatalog,
  freeModelCatalog.data, freeTierCatalog}.ts), the generic breaker
  (src/shared/utils/circuitBreaker.ts, adapted — see below), and the
  vendor lifecycle snapshot (config/quality/model-lifecycle.json).
- Closure: 556 files (~4.2 MB source tree). Static import walk over the
  decision + dispatch + breaker/state entries (relative + @/ +
  @omniroute/open-sse aliases, type-only imports erased as esbuild
  erases them): 0 unresolved imports, 0 files under app/server dirs —
  no Next.js route or dashboard code is included. The decision bundle
  itself pulls 397 files; dclawos/dispatch.ts, dclawos/breakerStore.ts
  and dclawos/stateStore.ts are NOT in the decision bundle (verified by
  the walk) — dispatch stays lazily loaded by the agent service.

## What this is — and what it is not

- IS: the combos CONFIG/SCORING layer (combo definitions, weights,
  tiers, scoring helpers) + the provider registry + network utilities
  the subset needs + the REAL 16-factor autoCombo scoring engine
  (scorePool / selectProvider / RouterStrategy registry / mode packs /
  self-healing / tier + specificity + intent classification).
- IS NOT (yet): the gateway dispatch core. Real request dispatch in
  upstream lives in open-sse/services/combo.ts (resolveComboTargets),
  open-sse/handlers/chatCore.ts, open-sse/executors/ and translators —
  still NOT vendored (too large; executors/translators load lazily per
  provider in the agent service, never in the decision bundle). The
  DclawOS decision function (dclawos/decision.ts, GPL DclawOS-written
  code that CALLS this subset) now scores with the real 16-factor
  scorePool, picks the winner via the requested RouterStrategy, and
  reports basis "autocombo-16-factor" with the winning factors. The
  dispatch skeleton (dclawos/dispatch.ts) names the pipeline stages
  (expand → prelude → score → order → attempt-with-retry → executor
  callback) for the agent service to call later; it executes nothing
  by itself.
- smartRouting.ts itself is NOT included: upstream it is an A2A skill
  that POSTs to the gateway over HTTP. Only its explanation output
  shape (routing_explanation string) is reused by the decision
  function; no HTTP call is carried over, and NO cost_envelope is
  emitted — no request has been executed at decision time, so no cost
  exists to report (fabricating one would be dishonest; cost
  materializes at execution time in the agent service).

## Phase 3 adaptations (not cuts) — every one carries a // reuse-check
comment in the adapted file naming the upstream symbol and the reason

- SQLite → JSON (analysis §3.2): taskFitness.ts layers 1–3
  (user_override / arena_elo / models_dev_tier) and the capability
  layer no longer read SQLite (src/lib/db/core.ts,
  src/lib/db/modelIntelligence.ts NOT adopted). They read JSON files
  under $DCLAWOS_FITNESS_DIR (default
  /var/lib/dclawos/engine-state/): fitness-overrides.json (GUI-owned
  user_override rows, written atomically by setUserFitnessOverride /
  clearUserFitnessOverride), fitness-intelligence.json (read-only
  snapshot for arena_elo / models_dev_tier rows; an absent or empty
  snapshot falls through to the static table, the designed behaviour
  for unknown models), fitness-capabilities.json (capability rows as a
  map or an array; the array form runs upstream's own aggregation rule
  — capability-maximal booleans, max context). Resolution ORDER and
  fall-through semantics are unchanged. intelligenceLookupCache.ts
  keeps miss-caching with a process-local write-generation counter
  (bumpIntelligenceGeneration) instead of the DB generation counter.
- Lifecycle snapshot as bundled data: modelLifecycle.ts and
  taskFitness.ts import config/quality/model-lifecycle.json instead of
  reading it via new URL(..., import.meta.url), which has nothing to
  resolve against inside the esbuild CJS bundle. The retired-id veto
  still applies (verified: gpt-5.2-codex reads retired).
- Nadir NOT registered (analysis §3.1/§3.7): routerStrategy.ts drops
  the nadir import; "nadir" falls back to "rules" like any unknown
  strategy. Nadir is an external HTTP decision API and must never enter
  the offline decideRouting path; port it later only as an opt-in.
- Breaker persistence moved up a layer (analysis §3.4):
  src/shared/utils/circuitBreaker.ts keeps the state machine verbatim
  but its domainState (SQLite) persistence calls are no-op stubs;
  dclawos/breakerStore.ts owns file persistence via the onStateChange
  hook (circuit-breakers.json, atomic writes) and restores snapshots
  through the breaker's public fields. dclawos/stateStore.ts owns
  self-healing.json (expired exclusions pruned on load),
  adaptation.json (upstream autoCombo/persistence.ts shape),
  quality.json and metrics.json (DclawOS-defined snapshot shapes —
  upstream routing/quality.ts persistence was not fully traced,
  analysis §3.8 item 3). A restored DEGRADED breaker scores as
  HALF_OPEN (0.5), never as dead (0.0) — see
  normalizeBreakerStateForScoring. The offline CLI never touches
  these files: missing state means neutral defaults.
- Lockout reads without the gateway file: resilienceCandidateFilter.ts
  and modelLockoutFilter.ts do NOT import accountFallback.ts (~2600
  lines + DB chain). dclawos/lockoutStore.ts implements the same READ
  semantics (in-memory provider:connection:model locks with expiry,
  canonical-provider aliasing, rateLimitedUntil account check) and the
  trace call goes to the no-op sink dclawos/autoTrace.ts (upstream
  tracing is best-effort; dropping it changes no filtering decision).
  Limitation, stated not hidden: locks recorded by upstream dispatch
  code under quota-scoped variant names are not visible to this store
  until accountFallback itself is ported (Stage D follow-up).
- CJS-bundle guards: tierResolver.ts builds its lazy require from
  __filename instead of import.meta.url (unavailable in the CJS
  bundle); the DB pricing refresh still fails open exactly as upstream
  does when the DB is absent, and setTierPricingSnapshot remains the
  injection point. manifestAdapter.ts keeps its combo import type-only
  (erased by esbuild); it does not drag in combo.ts.
- NOT ported in this stage (honest gaps, see analysis §3.8):
  chaosEngine.ts (testing tool; needs the combo/types + error.ts
  closure), accountFallback.ts (whole file; classifiers must stay
  together), combo.ts / combo/* (dispatch core — skeleton only),
  handlers/chatCore.ts, executors/*, translator/*, routing/quality.ts
  (quality factor stays candidate-supplied via telemetry),
  pipelineRouter.ts (multi-stage orchestration, out of scope),
  nadirStrategy.ts (opt-in only, see above). The three zero-weight
  factors (cacheAffinity, resetWindowAffinity, reliability) are all
  still computed — dropping them would be a feature cut.

## Decision interface (Phase 3, additive only)

decideRouting / buildCatalog keep their exact signatures and return
shapes. New optional inputs: taskType, promptMessages, weights,
modePack, routerStrategy, telemetry, budgetCap / budgetFallback,
explorationRate (default 0 for determinism; upstream default is 0.05
and applies only when the caller passes explorationRate > 0),
estimatedInputTokens, lastKnownGoodProvider, sla. New additive
outputs: factors (winner's 16 factors), isExploration, excluded,
modePack; ranked entries gain optional factors (never renamed).
Ranked order is pure scorePool order. Winner model is the winning
candidate's model when no model pin is given (previously the literal
"auto"); provider pins, unknown-provider rejection and all Phase 2
--selftest assertions are unchanged, with Phase 3 assertions added
alongside (16-factor sanity via telemetry, weight validation, mode
pack + strategy registry sanity, unknown-pack fallback, strict-budget
BudgetExceededError path).

## License

MIT (upstream). Files keep their original headers; the upstream LICENSE
is preserved here. MIT code may be combined into the GPL DclawOS work
(MIT is GPL-compatible); OmniRoute names, icons and trademarks are NOT
reused in DclawOS UI (MIT covers code, not trademarks).

## Build

Compiled at image build time (NOT a prebuilt blob) by hook
1003-dclawos-engine-build:
- Official Node 24 LTS tarball (v24.22.2, checksum-verified) extracted
  to /opt/dclawos-node (kept as the agent-service runtime) and symlinked
  as /usr/local/bin/dclawos-node. Debian bookworm's Node 18 is too old
  (upstream engines require >=22.22.2 <23 || >=24 <27).
- npm installs pinned build deps (esbuild 0.28.2 + the subset's
  runtime deps zod 4.6.7, undici 8.12.3, ws 8.21.3, socks 2.8.9,
  yazl 3.3.1, safe-regex 2.1.1) into a throwaway staging dir.
- esbuild bundles dclawos/decision.ts (resolving the @/ and
  @omniroute/open-sse path aliases) into /usr/share/dclawos/engine/
  decision.js; runtime deps are bundled, so no node_modules ships.
- The hook then runs dclawos-route --catalog to write
  /usr/share/dclawos/engine/catalog.json (validated as JSON) and
  dclawos-route --selftest as a real behavior gate. Any step failing
  fails the whole build; the engine is never silently skipped.

## Phase 4 module 1 additions

- `dclawos/llm.ts` + `dclawos/llmClaude.ts` (GPL, DclawOS-written):
  the LLM execution path. Bundled separately as `engine/llm.js`
  (decision.js stays dispatch-free), invoked via the `dclawos-llm`
  CLI. Registry-driven executor (OpenAI-shaped providers directly;
  Anthropic via the adapted translator branch in llmClaude.ts),
  dispatch() attempt loop, cost from real response usage x
  `providerCostData` pricing, `cost_envelope` emitted per executed
  request. Build hook runs `dclawos-llm --selftest` against a local
  mock endpoint; no real keys exist in the build.
