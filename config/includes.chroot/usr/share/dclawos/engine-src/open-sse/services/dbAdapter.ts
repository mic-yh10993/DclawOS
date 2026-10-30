/*
 * DclawOS DB adapter (GPL-2.0-or-later; DclawOS-written).
 * Phase 4 module 3 (gap row B1).
 *
 * The ONLY app-DB touchpoints of the accountFallback transplant
 * closure, mapped onto DclawOS's established file-state store
 * (dclawos/stateStore.ts pattern: one JSON file under the state
 * dir, atomic replace, same fields). This is the documented
 * adaptation the Phase 4 inventory prescribes ("map them in ONE
 * adapter file"); no copied file talks to a database.
 *
 * - setConnectionRateLimitUntil(connectionId, untilIso): upstream
 *   writes the provider_connections.rate_limit_until column. Here
 *   the same value is persisted per connection id in the file
 *   store; getConnectionRateLimitUntil reads it back. Same field,
 *   same semantics, different storage engine (files, not SQLite) —
 *   functionally equivalent for the fallback logic, which only
 *   needs the persisted timestamp.
 * - getRawProviderConnections(): upstream reads connection rows
 *   from the gateway DB. DclawOS has no connection DB (providers
 *   are registry entries + secret files), so this returns an
 *   empty list: cooldownManager then seeds nothing from storage
 *   and connection-level behaviour degrades to the provider-level
 *   state this module persists — the honest local equivalent, not
 *   a silent stub: callers treat "no rows" as "no stored
 *   connections", which is factually true here.
 */

import { readFileSync, writeFileSync, mkdirSync, renameSync } from "fs";
import { dirname } from "path";

function statePath(): string {
  return process.env.DCLAWOS_STATE_DIR
    ? `${process.env.DCLAWOS_STATE_DIR}/connection-rate-limits.json`
    : "/var/lib/dclawos/engine-state/connection-rate-limits.json";
}

interface RateLimitState {
  rateLimitUntil: Record<string, string>;
}

function loadState(): RateLimitState {
  try {
    const parsed = JSON.parse(readFileSync(statePath(), "utf-8"));
    if (parsed && typeof parsed.rateLimitUntil === "object") {
      return { rateLimitUntil: parsed.rateLimitUntil ?? {} };
    }
  } catch {
    /* missing/corrupt state = empty state */
  }
  return { rateLimitUntil: {} };
}

function saveState(state: RateLimitState): void {
  const path = statePath();
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.tmp.${process.pid}`;
  writeFileSync(tmp, JSON.stringify(state, null, 2) + "\n", "utf-8");
  renameSync(tmp, path);
}

export async function setConnectionRateLimitUntil(
  connectionId: string,
  untilIso: string | null
): Promise<void> {
  const state = loadState();
  if (untilIso === null) delete state.rateLimitUntil[connectionId];
  else state.rateLimitUntil[connectionId] = untilIso;
  saveState(state);
}

export function getConnectionRateLimitUntil(
  connectionId: string
): string | null {
  return loadState().rateLimitUntil[connectionId] ?? null;
}

export async function getRawProviderConnections(
  _filter?: unknown,
  _limit?: number,
  _offset?: number,
  _columns?: unknown
): Promise<unknown[]> {
  return [];
}
