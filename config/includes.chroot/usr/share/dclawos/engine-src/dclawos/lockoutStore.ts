/*
 * DclawOS model-lockout / account-unavailability store (GPL-2.0-or-later;
 * DclawOS-written code).
 *
 * READ semantics mirror upstream open-sse/services/accountFallback.ts
 * (isModelLocked / isAccountUnavailable / cooldownUntilMs / lockModel), which
 * the Stage A filters need — without importing that file, because it drags
 * the gateway SQLite chain (src/lib/db/providers, settings DB, dashboard
 * code) into the decision bundle.
 *
 * // reuse-check: accountFallback.ts isModelLocked NOT adopted verbatim,
 *   reason: its key derivation consults quota-scoped model families
 *   (getQuotaScopedModelForProvider, getCodexModelScope, exactModelLock) that
 *   live in the dispatch layer. This store keys locks
 *   "<canonical-provider>:<connectionId>:<model>" (canonical via
 *   resolveProviderId, so "cx" and "codex" share state, as upstream does)
 *   and checks the canonical key, the lowercased-model key and the raw
 *   provider key. Writer and reader agree on this format inside DclawOS, so
 *   a lock written here is always visible here; a lock written by upstream
 *   dispatch code under a quota-scoped variant name is NOT visible until
 *   the accountFallback port lands (Stage D follow-up, flagged in README).
 * // reuse-check: accountFallback.ts lockModel longer-cooldown-wins rule IS
 *   adopted (an existing lock with more time remaining is preserved).
 * // reuse-check: accountFallback.ts cooldownUntilMs / isAccountUnavailable
 *   ARE adopted (pure functions: numeric string, ISO date, or Date object;
 *   unavailable iff the instant is in the future).
 *
 * Deliberate difference from upstream: no 15 s cleanup timer. Expiry is lazy
 * (checked on every read), so importing this module can never keep a CLI
 * process alive — the offline dclawos-route path must exit promptly.
 */

import { resolveProviderId } from "../src/shared/constants/providers";

export interface ModelLockoutEntry {
  reason: string;
  until: number;
  lockedAt: number;
  failureCount: number;
  lastFailureAt?: number;
  resetAfterMs?: number;
}

const lockouts = new Map<string, ModelLockoutEntry>();

function canonicalProvider(provider: string): string {
  try {
    const resolved = resolveProviderId(provider);
    return typeof resolved === "string" && resolved.length > 0 ? resolved : provider;
  } catch {
    return provider;
  }
}

function lockKeys(provider: string, connectionId: string, model: string): string[] {
  const canonical = canonicalProvider(provider);
  const keys = [
    `${canonical}:${connectionId}:${model}`,
    `${canonical}:${connectionId}:${model.toLowerCase()}`,
  ];
  if (canonical !== provider) keys.push(`${provider}:${connectionId}:${model}`);
  return keys;
}

function cleanupKey(key: string, now = Date.now()): void {
  const entry = lockouts.get(key);
  if (entry && now > entry.until) lockouts.delete(key);
}

export function lockModel(
  provider: string,
  connectionId: string,
  model: string | null | undefined,
  reason: string,
  cooldownMs: number,
  metadata: Partial<ModelLockoutEntry> = {}
): void {
  if (!model) return;
  const key = lockKeys(provider, connectionId, model)[0];
  cleanupKey(key);
  const now = Date.now();
  const newUntil = now + cooldownMs;
  const existing = lockouts.get(key);
  if (existing && existing.until > newUntil) {
    if (metadata.failureCount && metadata.failureCount > existing.failureCount) {
      existing.failureCount = metadata.failureCount;
      existing.lastFailureAt = metadata.lastFailureAt ?? existing.lastFailureAt;
      existing.resetAfterMs = metadata.resetAfterMs ?? existing.resetAfterMs;
      lockouts.set(key, existing);
    }
    return;
  }
  lockouts.set(key, {
    reason,
    until: newUntil,
    lockedAt: now,
    failureCount: metadata.failureCount ?? existing?.failureCount ?? 1,
    lastFailureAt: metadata.lastFailureAt ?? now,
    resetAfterMs: metadata.resetAfterMs ?? existing?.resetAfterMs ?? 0,
  });
}

export function isModelLocked(
  provider: string,
  connectionId: string,
  model: string | null | undefined
): boolean {
  if (!model) return false;
  const now = Date.now();
  for (const key of lockKeys(provider, connectionId, model)) {
    cleanupKey(key, now);
    if (lockouts.has(key)) return true;
  }
  return false;
}

export function getModelLockoutInfo(
  provider: string,
  connectionId: string,
  model: string | null | undefined
): { reason: string; remainingMs: number; lockedAt: string; failureCount: number } | null {
  if (!model) return null;
  for (const key of lockKeys(provider, connectionId, model)) {
    cleanupKey(key);
    const entry = lockouts.get(key);
    if (entry) {
      return {
        reason: entry.reason,
        remainingMs: entry.until - Date.now(),
        lockedAt: new Date(entry.lockedAt).toISOString(),
        failureCount: entry.failureCount,
      };
    }
  }
  return null;
}

export function clearModelLockouts(): void {
  lockouts.clear();
}

const COOLDOWN_NUMERIC_RE = /^-?\d+(\.\d+)?$/;

export function cooldownUntilMs(value: string | number | Date | null | undefined): number {
  if (value === null || value === undefined || value === "") return NaN;
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value;
  const raw = value.trim();
  if (COOLDOWN_NUMERIC_RE.test(raw)) return Number(raw);
  return new Date(raw).getTime();
}

export function isAccountUnavailable(
  unavailableUntil: string | Date | null | undefined
): boolean {
  if (!unavailableUntil) return false;
  const ms = cooldownUntilMs(unavailableUntil);
  return Number.isFinite(ms) && ms > Date.now();
}

export function getUnavailableUntil(cooldownMs: number): string {
  return new Date(Date.now() + cooldownMs).toISOString();
}
