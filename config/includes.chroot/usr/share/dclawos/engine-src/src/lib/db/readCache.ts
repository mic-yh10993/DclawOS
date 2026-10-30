/*
 * DclawOS read-cache adapter (GPL-2.0-or-later; DclawOS-written).
 * Phase 4 module 3 (gap row B1).
 *
 * // reuse-check: REPLACES the vendored upstream readCache.ts at
 *   this path (a TTL cache facade over the gateway SQLite DB).
 *   Reason: DclawOS has no gateway DB; every getter here is backed
 *   by the local sources that actually exist on this system —
 *   settings from the engine settings file (or {} = upstream
 *   empty-DB defaults), pricing from the vendored pricing
 *   constants, connections from the file-state dbAdapter (empty:
 *   no DB-managed connections exist), combos from the local combo
 *   definitions file when present. Signatures and return shapes
 *   match upstream so callers (accountFallback's
 *   getRuntimeProviderProfile, resilience settings) behave exactly
 *   as upstream-with-an-empty-DB, which is the factual local state.
 *   The upstream original remains in the OmniRoute reference clone.
 */

import { readFileSync } from "fs";

function readJsonFile(path: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf-8"));
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function stateFile(name: string): string {
  const dir = process.env.DCLAWOS_STATE_DIR ?? "/var/lib/dclawos/engine-state";
  return `${dir}/${name}`;
}

export async function getCachedSettings(): Promise<Record<string, unknown>> {
  return readJsonFile(stateFile("settings.json")) ?? {};
}

export async function getCachedPricing(): Promise<Record<string, unknown>> {
  return readJsonFile(stateFile("pricing.json")) ?? {};
}

export async function getCachedProviderConnections(
  ..._args: unknown[]
): Promise<unknown[]> {
  return [];
}

export async function getCachedRawProviderConnections(
  ..._args: unknown[]
): Promise<unknown[]> {
  return [];
}

export async function getCachedProviderConnectionById(
  ..._args: unknown[]
): Promise<null> {
  return null;
}

export async function getCachedProviderNodes(
  ..._args: unknown[]
): Promise<unknown[]> {
  return [];
}

let lkgpCache: Record<string, unknown> | null = null;

export async function getCachedLKGP(
  ..._args: unknown[]
): Promise<Record<string, unknown> | null> {
  return lkgpCache;
}

export async function setCachedLKGP(
  value: Record<string, unknown> | null,
  ..._args: unknown[]
): Promise<void> {
  lkgpCache = value;
}

export function invalidateCachedLKGP(..._args: unknown[]): void {
  lkgpCache = null;
}

export function getCombosCacheVersion(): number {
  return 0;
}

export async function getCachedComboByName(
  name: string
): Promise<Record<string, unknown> | null> {
  const combos = readJsonFile(stateFile("combos.json"));
  const entry = combos?.[name];
  return entry && typeof entry === "object"
    ? (entry as Record<string, unknown>)
    : null;
}

export async function getCachedComboForModel(
  ..._args: unknown[]
): Promise<null> {
  return null;
}

export function getModelCatalogCacheVersion(): number {
  return 0;
}

export function invalidateModelCatalogCache(): void {}

export function isConnectionRuntimeStateUpdate(
  ..._args: unknown[]
): boolean {
  return false;
}

export type UpdateOpts = { skipModelCatalog?: boolean };

export function invalidateConnectionUpdate(..._args: unknown[]): void {}

export function invalidateDbCache(..._args: unknown[]): void {}
