/**
 * Memo for model_intelligence lookups on the auto/* scoring path (#16814).
 *
 * Caches misses as well as hits: with an empty/partial table every candidate used to issue
 * up to three SELECTs per scoring pass. The cache is dropped whenever any model_intelligence
 * write happens (see getModelIntelligenceWriteGeneration), so a cached miss can never hide a
 * freshly synced arena_elo row or a user override.
 */
// reuse-check (DclawOS adaptation, §3.2):
// getModelIntelligenceWriteGeneration (src/lib/db/modelIntelligenceGeneration.ts)
// is NOT adopted: it counts writes to the gateway SQLite model_intelligence
// table. Replaced by a process-local counter; the DclawOS fitness store bumps
// it whenever fitness-overrides.json changes, so a cached miss can never hide
// a freshly written user override. Miss-caching and clear semantics unchanged.
let _writeGeneration = 0;
export function getModelIntelligenceWriteGeneration(): number {
  return _writeGeneration;
}
export function bumpIntelligenceGeneration(): void {
  _writeGeneration++;
}

const cache = new Map<string, number | null>();
let seenGeneration = -1;

export function lookupIntelligenceCached(key: string, load: () => number | null): number | null {
  const generation = getModelIntelligenceWriteGeneration();
  if (generation !== seenGeneration) {
    cache.clear();
    seenGeneration = generation;
  }
  if (cache.has(key)) return cache.get(key)!;
  const value = load();
  cache.set(key, value);
  return value;
}

export function clearIntelligenceLookupCache(): void {
  cache.clear();
}
