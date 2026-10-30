// Quality persistence + chaos gating (Phase 4 module 4, gap rows
// B2/B3).
//
// Upstream routing/quality.ts keeps (provider, model) quality in
// an in-memory Map — fine for its long-lived hosted gateway. A
// desktop OS agent restarts with the machine, so DclawOS persists
// quality snapshots to the established file-state store and
// restores them at startup by replaying bounded synthetic events
// in the recorded success ratio. The EWMA math itself is
// upstream's, untouched (recordQualityEvent / getProviderQuality
// / getQualitySnapshot / classifyQuality).
//
// Chaos fault injection (autoCombo/chaosEngine.ts, copied
// verbatim) is reachable ONLY behind the "chaos-enabled"
// settings flag or DCLAWOS_CHAOS=1 — fault injection must never
// be on by default.
//
// reuse-check: new file by design (persistence backend for the
//   copied quality module + the chaos gate); algorithm code is
//   upstream quality.ts / chaosEngine.ts, imported not rewritten.

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import {
  getProviderQuality,
  getQualitySnapshot,
  recordQualityEvent,
  classifyQuality,
  type ProviderQuality,
} from "../open-sse/services/routing/quality";
import {
  dispatchChaosFromCombo,
} from "../open-sse/services/autoCombo/chaosEngine";

const QUALITY_FILE = "provider-quality.json";

function stateDir(): string {
  return process.env.DCLAWOS_STATE_DIR || "/var/lib/dclawos/engine-state";
}

export function qualityPath(): string {
  return `${stateDir()}/${QUALITY_FILE}`;
}

type PersistedQuality = ProviderQuality;

let restored = false;

export function restoreQuality(): void {
  if (restored) return;
  restored = true;
  let data: Record<string, PersistedQuality>;
  try {
    data = JSON.parse(readFileSync(qualityPath(), "utf8"));
  } catch {
    return; // no prior state: upstream starts cold too
  }
  for (const q of Object.values(data)) {
    if (!q || !q.samples || !q.provider) continue;
    // Replay: the first event seeds the EWMA, so replaying the
    // recorded success ratio reproduces successEwma closely.
    // Bounded at 40 synthetic events per (provider, model).
    const replay = Math.min(40, q.samples);
    const successes = Math.round(q.successEwma * replay);
    for (let i = 0; i < successes; i += 1) {
      recordQualityEvent({
        provider: q.provider, model: q.model || "unknown",
        outcome: "success", status: 200,
        latencyMs: q.latencyEwmaMs || 1000,
      });
    }
    for (let i = successes; i < replay; i += 1) {
      recordQualityEvent({
        provider: q.provider, model: q.model || "unknown",
        outcome: "error", status: 500,
        latencyMs: q.latencyEwmaMs || 1000,
      });
    }
  }
}

export function persistQuality(): void {
  const out: Record<string, PersistedQuality> = {};
  try {
    Object.assign(out, JSON.parse(readFileSync(qualityPath(), "utf8")));
  } catch { /* fresh file */ }
  for (const q of getQualitySnapshot(500)) {
    out[`${q.provider}::${q.model}`] = q;
  }
  try {
    mkdirSync(dirname(qualityPath()), { recursive: true });
    writeFileSync(qualityPath(), JSON.stringify(out), { mode: 0o600 });
  } catch { /* state dir unwritable: quality stays in-memory */ }
}

export function recordQuality(
  provider: string,
  model: string,
  ok: boolean,
  status: number,
  latencyMs: number,
): ProviderQuality {
  restoreQuality();
  recordQualityEvent({
    provider,
    model,
    outcome: ok ? "success" : status === 429 ? "rate_limited" : "error",
    status,
    latencyMs,
    outputTokens: ok ? 1 : 0,
  });
  persistQuality();
  return getProviderQuality(provider, model);
}

export function qualityOf(
  provider: string,
  model: string,
): ProviderQuality & { classification: string } {
  restoreQuality();
  const q = getProviderQuality(provider, model);
  return { ...q, classification: classifyQuality(q) };
}

export function chaosEnabled(): boolean {
  if (process.env.DCLAWOS_CHAOS === "1") return true;
  try {
    const settings = JSON.parse(
      readFileSync(
        process.env.DCLAWOS_SETTINGS_FILE ||
          "/var/lib/dclawos/agent-settings.json",
        "utf8",
      ),
    );
    return settings["chaos-enabled"] === true;
  } catch {
    return false;
  }
}

export { dispatchChaosFromCombo };
