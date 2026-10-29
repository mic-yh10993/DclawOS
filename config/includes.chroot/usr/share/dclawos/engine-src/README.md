# DclawOS routing engine source (Phase 2 module 4)

## Source

Stripped subset of OmniRoute (MIT; upstream LICENSE preserved as
LICENSE.UPSTREAM-OMNIROUTE in this directory):
- Upstream: diegosouzapw/OmniRoute (canonical cheaper-inference/OmniRoute;
  the old name redirects), package version 3.8.53, shallow-clone HEAD
  5332fe88 at strip time.
- Seeds: src/lib/combos/{intelligentRouting, steps, compositeTiers,
  snapshotWeights, controlCenter}.ts.
- Closure: 497 files (~2.2 MB source), computed by a static import walk
  (relative + @/ + @omniroute/open-sse aliases). 0 unresolved imports,
  0 files under app/server dirs — no Next.js route or dashboard code
  is included.

## What this is — and what it is not

- IS: the combos CONFIG/SCORING layer (combo definitions, weights,
  tiers, scoring helpers) + the provider registry + network utilities
  the subset needs.
- IS NOT: the gateway dispatch core. Real request dispatch in upstream
  lives in open-sse/services/combo.ts (resolveComboTargets),
  open-sse/handlers/chatCore.ts, open-sse/executors/ and translators —
  deliberately NOT vendored here (too large, against the lightweight
  requirement). The DclawOS decision function (dclawos/decision.ts,
  GPL DclawOS-written code that CALLS this subset) filters by strategy
  → scores via buildIntelligentProviderScores → ranks → explains in
  the smartRouting explanation shape; it does not copy gateway
  dispatch.
- smartRouting.ts itself is NOT included: upstream it is an A2A skill
  that POSTs to the gateway over HTTP. Only its explanation output
  shape (routing_explanation string) is reused by the decision
  function; no HTTP call is carried over, and NO cost_envelope is
  emitted — no request has been executed at decision time, so no cost
  exists to report (fabricating one would be dishonest; cost
  materializes at execution time in the agent service).

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
