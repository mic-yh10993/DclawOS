/*
 * DclawOS circuit-breaker persistence (GPL-2.0-or-later; DclawOS-written
 * code). Stage C (§3.4): wraps (does not fork) the upstream CircuitBreaker.
 *
 * Upstream's class persists to the gateway SQLite DB (domainState); the
 * DclawOS vendored copy stubs that out (see its reuse-check comment), and
 * THIS module owns the file replacement: the constructor's onStateChange
 * hook snapshots the breaker into circuit-breakers.json (debounced, via the
 * stateStore's atomic write), and getPersistentBreaker restores a snapshot
 * into a fresh breaker through the class's PUBLIC fields (state,
 * failureCount, lastFailureTime, openCycleCount, kindFailureCounts) — the
 * same fields _restoreFromDb used to restore upstream. A restored DEGRADED
 * state scores as HALF_OPEN (see normalizeBreakerStateForScoring); the
 * breaker object itself keeps DEGRADED until its own transitions move it.
 *
 * Only the agent service imports this module. The offline CLI path never
 * touches breaker state: a missing snapshot file means "no history", and
 * scoring proceeds with neutral defaults.
 */

import {
  getAllCircuitBreakerStatuses,
  getCircuitBreaker,
  type CircuitBreaker,
} from "../src/shared/utils/circuitBreaker";

/**
 * Upstream's CircuitBreakerOptions interface is not exported, so this is the
 * DclawOS-local subset getPersistentBreaker accepts (the fields it forwards).
 */
export interface PersistentBreakerOptions {
  failureThreshold?: number;
  resetTimeout?: number;
  halfOpenRequests?: number;
  onStateChange?: ((name: string, oldState: string, newState: string) => void) | null;
  isFailure?: (error: unknown) => boolean;
}
import {
  loadBreakerSnapshot,
  saveBreakerSnapshot,
  type BreakerSnapshotEntry,
} from "./stateStore";

const restoredNames = new Set<string>();
let flushTimer: ReturnType<typeof setTimeout> | null = null;

export function snapshotBreakerToFile(breaker: CircuitBreaker): boolean {
  try {
    const status = breaker.getStatus() as unknown as Record<string, unknown>;
    const entry: BreakerSnapshotEntry = {
      state: status.state as BreakerSnapshotEntry["state"],
      failureCount: Number(status.failureCount ?? 0),
      lastFailureTime:
        typeof status.lastFailureTime === "number" ? status.lastFailureTime : null,
      openCycleCount: Number(status.openCycleCount ?? 0),
      kindFailureCounts: (status.kindFailureCounts as Record<string, number>) ?? {},
      transitionHistory: Array.isArray(status.transitionHistory)
        ? (status.transitionHistory as BreakerSnapshotEntry["transitionHistory"]).slice(-50)
        : [],
      effectiveResetTimeout: Number(status.effectiveResetTimeout ?? status.resetTimeout ?? 0),
    };
    return saveBreakerSnapshot(breaker.name, entry);
  } catch {
    return false;
  }
}

function scheduleFlush(breaker: CircuitBreaker): void {
  if (flushTimer !== null) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    snapshotBreakerToFile(breaker);
  }, 500);
  const timer = flushTimer as unknown as { unref?: () => void };
  if (typeof timer.unref === "function") timer.unref();
}

export function flushAllBreakerSnapshots(): void {
  try {
    for (const status of getAllCircuitBreakerStatuses()) {
      const name = (status as unknown as { name?: string }).name;
      if (typeof name === "string") {
        const breaker = getCircuitBreaker(name);
        snapshotBreakerToFile(breaker);
      }
    }
  } catch {
    // Snapshot flush must never break dispatch.
  }
}

function restoreSnapshotInto(breaker: CircuitBreaker): void {
  const snapshot = loadBreakerSnapshot(breaker.name);
  if (!snapshot) return;
  const target = breaker as unknown as Record<string, unknown>;
  const valid = ["CLOSED", "DEGRADED", "OPEN", "HALF_OPEN"];
  if (valid.includes(snapshot.state)) target.state = snapshot.state;
  target.failureCount = snapshot.failureCount;
  target.lastFailureTime = snapshot.lastFailureTime;
  target.openCycleCount = snapshot.openCycleCount;
  target.kindFailureCounts = { ...snapshot.kindFailureCounts };
  if (snapshot.state === "HALF_OPEN") {
    target.halfOpenAllowed = (breaker as unknown as { halfOpenRequests?: number })
      .halfOpenRequests ?? 1;
  }
}

/**
 * Return the (singleton) breaker for `name`, restoring its last persisted
 * snapshot exactly once per process and snapshotting every later state
 * change to circuit-breakers.json. The caller's onStateChange, if given,
 * still runs first.
 */
export function getPersistentBreaker(
  name: string,
  options: PersistentBreakerOptions = {}
): CircuitBreaker {
  const callerHook = options.onStateChange ?? null;
  const breaker = getCircuitBreaker(name, {
    ...options,
    onStateChange: (breakerName: string, oldState: string, newState: string) => {
      try {
        if (callerHook) callerHook(breakerName, oldState, newState);
      } finally {
        scheduleFlush(getCircuitBreaker(breakerName));
      }
    },
  });
  if (!restoredNames.has(name)) {
    restoredNames.add(name);
    restoreSnapshotInto(breaker);
  }
  return breaker;
}

/** Provider-connection breaker name (upstream connectionCircuitBreaker.ts). */
export function connectionBreakerName(provider: string, connectionId: string): string {
  return `${provider}::conn::${connectionId}`;
}
