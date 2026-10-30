/*
 * DclawOS engine-state file store (GPL-2.0-or-later; DclawOS-written code).
 *
 * Stage C (§3.4): file-based local state for the agent service. No gateway,
 * no SQLite. All files live under $DCLAWOS_ENGINE_STATE_DIR (default
 * /var/lib/dclawos/engine-state/). Every write is tmp-file + rename (atomic
 * for readers); a failed write returns false and never throws; a missing or
 * corrupt file reads as the caller's fallback (fail-open to neutral scoring),
 * never as a routing crash. No file here ever holds credentials, tokens, or
 * API keys — breaker state is counts and timestamps only.
 *
 * Shapes mirror upstream autoCombo/persistence.ts (AdaptationState) where one
 * exists; quality/metrics shapes are DclawOS-defined snapshots (upstream
 * routing/quality.ts persistence was not fully traced — analysis §3.8 item 3
 * — so no upstream shape is claimed for them).
 *
 * The OFFLINE CLI path (dclawos-route) never imports this module: missing
 * state files mean neutral defaults there. Only the agent service owns
 * read-write state.
 */

import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

export function resolveEngineStateDir(): string {
  return process.env.DCLAWOS_ENGINE_STATE_DIR || "/var/lib/dclawos/engine-state";
}

export function readStateJson<T>(file: string, fallback: T): T {
  try {
    const raw = readFileSync(join(resolveEngineStateDir(), file), "utf8");
    const parsed = JSON.parse(raw) as T;
    return parsed === undefined || parsed === null ? fallback : parsed;
  } catch {
    return fallback;
  }
}

/** Atomic write (tmp + rename). Returns false instead of throwing. */
export function writeStateJsonAtomic(file: string, value: unknown): boolean {
  try {
    const dir = resolveEngineStateDir();
    mkdirSync(dir, { recursive: true });
    const tmp = join(dir, `.${file}.tmp-${process.pid}`);
    writeFileSync(tmp, JSON.stringify(value, null, 2), "utf8");
    try {
      // Best-effort fsync of the tmp file before rename.
      const handle = openSync(tmp, "r");
      try {
        fsyncSync(handle);
      } finally {
        closeSync(handle);
      }
    } catch {
      // fsync is best-effort; the rename below still gives atomicity.
    }
    renameSync(tmp, join(dir, file));
    return true;
  } catch {
    return false;
  }
}

// ─── Circuit breakers (§3.4 circuit-breakers.json) ─────────────────────────

export type StoredBreakerState = "CLOSED" | "DEGRADED" | "OPEN" | "HALF_OPEN";

export interface BreakerSnapshotEntry {
  state: StoredBreakerState;
  failureCount: number;
  lastFailureTime: number | null;
  openCycleCount: number;
  kindFailureCounts: Record<string, number>;
  transitionHistory: Array<{
    from: string;
    to: string;
    timestamp: number;
    failureCount: number;
    reason?: string;
  }>;
  effectiveResetTimeout: number;
}

export type BreakerSnapshotMap = Record<string, BreakerSnapshotEntry>;

export function loadBreakerSnapshots(): BreakerSnapshotMap {
  return readStateJson<BreakerSnapshotMap>("circuit-breakers.json", {});
}

export function loadBreakerSnapshot(name: string): BreakerSnapshotEntry | null {
  const all = loadBreakerSnapshots();
  return all[name] ?? null;
}

export function saveBreakerSnapshot(name: string, entry: BreakerSnapshotEntry): boolean {
  const all = loadBreakerSnapshots();
  all[name] = entry;
  return writeStateJsonAtomic("circuit-breakers.json", all);
}

/**
 * Scoring-side normalisation (§3.7 DEGRADED gap): calculateFactors only
 * handles CLOSED/HALF_OPEN/OPEN, so a restored DEGRADED must NOT fall into
 * its else branch (which scores 0.0, "dead"). DEGRADED maps to HALF_OPEN
 * (0.5) — degraded, not dead. This mapping is explicit, not hidden.
 */
export function normalizeBreakerStateForScoring(
  state: string | undefined | null
): "CLOSED" | "HALF_OPEN" | "OPEN" {
  if (state === "CLOSED") return "CLOSED";
  if (state === "OPEN") return "OPEN";
  return "HALF_OPEN";
}

// ─── Self-healing exclusions (§3.4 self-healing.json) ─────────────────────

export interface StoredExclusion {
  provider: string;
  excludedAt: number;
  cooldownMs: number;
  reason: string;
  probeCount: number;
}

export interface SelfHealingState {
  exclusions: StoredExclusion[];
  incidentMode: boolean;
}

/**
 * Expired exclusions are dropped on load: remaining-cooldown recomputation
 * (Date.now() - excludedAt > cooldownMs) makes a stale restore safe — an
 * exclusion that expired while the service was down never blocks routing.
 */
export function loadSelfHealingState(): SelfHealingState {
  const raw = readStateJson<SelfHealingState>("self-healing.json", {
    exclusions: [],
    incidentMode: false,
  });
  const now = Date.now();
  const exclusions = (Array.isArray(raw.exclusions) ? raw.exclusions : []).filter(
    (e) => typeof e?.provider === "string" && now - e.excludedAt <= e.cooldownMs
  );
  return { exclusions, incidentMode: raw.incidentMode === true };
}

export function saveSelfHealingState(state: SelfHealingState): boolean {
  return writeStateJsonAtomic("self-healing.json", state);
}

// ─── Adaptation state (upstream autoCombo/persistence.ts shape) ───────────

export interface AdaptationState {
  comboId: string;
  providerScores: Record<string, number>;
  exclusionHistory: Array<{
    provider: string;
    excludedAt: string;
    cooldownMs: number;
    reason: string;
  }>;
  modePackHistory: Array<{ pack: string; activatedAt: string }>;
  totalDecisions: number;
  explorationHits: number;
  lastUpdated: string;
}

export function loadAdaptationState(comboId: string): AdaptationState | null {
  const all = readStateJson<Record<string, AdaptationState>>("adaptation.json", {});
  return all[comboId] ?? null;
}

export function saveAdaptationState(state: AdaptationState): boolean {
  const all = readStateJson<Record<string, AdaptationState>>("adaptation.json", {});
  all[state.comboId] = { ...state, lastUpdated: new Date().toISOString() };
  return writeStateJsonAtomic("adaptation.json", all);
}

// ─── Quality + metrics snapshots (DclawOS-defined shapes) ─────────────────

export interface QualityEntry {
  quality: number;
  samples: number;
  updatedAt: string;
}

export type QualityState = Record<string, QualityEntry>; // key "<provider>::<model>"

export function loadQualityState(): QualityState {
  return readStateJson<QualityState>("quality.json", {});
}

export function saveQualityState(state: QualityState): boolean {
  return writeStateJsonAtomic("quality.json", state);
}

export function recordQualitySample(
  provider: string,
  model: string,
  quality: number
): QualityState {
  const state = loadQualityState();
  const key = `${provider}::${model}`;
  const prev = state[key];
  const samples = (prev?.samples ?? 0) + 1;
  // Running mean, so one sample never overwrites history.
  const next = prev ? (prev.quality * prev.samples + quality) / samples : quality;
  state[key] = { quality: next, samples, updatedAt: new Date().toISOString() };
  saveQualityState(state);
  return state;
}

export interface MetricsEntry {
  avgLatencyMs: number;
  successRate: number;
  totalRequests: number;
  updatedAt: string;
}

export type MetricsState = Record<string, MetricsEntry>; // key "<provider>::<model>"

export function loadMetricsState(): MetricsState {
  return readStateJson<MetricsState>("metrics.json", {});
}

export function saveMetricsState(state: MetricsState): boolean {
  return writeStateJsonAtomic("metrics.json", state);
}

export function recordMetricsSample(
  provider: string,
  model: string,
  latencyMs: number,
  success: boolean
): MetricsState {
  const state = loadMetricsState();
  const key = `${provider}::${model}`;
  const prev = state[key];
  const totalRequests = (prev?.totalRequests ?? 0) + 1;
  const avgLatencyMs = prev
    ? (prev.avgLatencyMs * prev.totalRequests + latencyMs) / totalRequests
    : latencyMs;
  const successRate = prev
    ? (prev.successRate * prev.totalRequests + (success ? 1 : 0)) / totalRequests
    : success
      ? 1
      : 0;
  state[key] = { avgLatencyMs, successRate, totalRequests, updatedAt: new Date().toISOString() };
  saveMetricsState(state);
  return state;
}

export function stateFileExists(file: string): boolean {
  try {
    return existsSync(join(resolveEngineStateDir(), file));
  } catch {
    return false;
  }
}
