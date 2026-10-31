# DclawOS Phase 6 — Push Architecture + WebView UI Rebuild

User directives behind this phase:
- 2026-10-31 08:36: monitoring must become PUSH — the agent
  reports its state, the system returns the agent's commands and
  real-time output, and human-side programs stop polling.
- 2026-10-31 08:54: UI route is hybrid — GTK4+libadwaita base,
  high-interaction pages (dashboard timeline / chat / approval
  overlay) as local WebView with a host bridge (message channel
  for commands + pushed event forwarding), interactive but light.

Status vocabulary: OPEN · WIP · CLOSED · BLOCKED-NAMED ·
DEFERRED-NAMED (explicit boundary recorded, not faked).

## M1 — Event bus, live output, task wake-up, bridge foundation

State: **CLOSED** (this module; build verification in the ledger
row below).

### Architecture

- `eventbus.py` (new, stdlib only): versioned (protocol v1)
  Unix-domain-socket event bus at `/var/lib/dclawos/eventbus.sock`
  (env override `DCLAWOS_EVENTBUS_SOCKET`). The agent daemon owns
  and serves it (threaded `EventBusServer`, thread-safe subscriber
  set). Wire format: newline-delimited typed JSON with explicit
  kinds — `hello` / `subscribe` / `publish` from clients, `ack` /
  `event` / `error` from the server. Malformed lines, unknown
  kinds and oversized lines fail closed for that connection and
  never kill the server; there is no eval or dynamic dispatch.
- Permissions follow the existing dclawos-session exchange
  pattern: state dir group `dclawos-session` mode 0770, socket
  0660 — human UI processes (group members) subscribe, agent-side
  producers publish.
- `agentlib.log_event` writes the durable `events.jsonl` append
  log FIRST (envelope unchanged: ts/type/summary/session, plus a
  new unique `id` and optional `task_id`/`data`), then publishes
  to the bus best-effort through `eventbus.publish_event`. A dead
  bus never blocks or loses a durable event. All producers that
  use `log_event` (daemon, broker via dclawos-privileged,
  provisioning paths) and `dclawos-log-event` reach the bus
  through this same helper.
- Takeover freshness: `takeoverlib.save_request` — the single
  shared write path for creation (daemon, takeover-request CLI)
  and resolution (`write_result` from the popup) — publishes a
  bus-only takeover event (`data.display: false`). The dashboard
  re-counts pending requests when one arrives. Likewise
  `agentlib.save_task` publishes a bus-only task signal, and
  `dclawos-agent-ctl` stop/resume publishes a stop event.

### Live command output

- The daemon's `shell` tool (`_run_shell`) now runs the command
  with `Popen` and two reader threads: stdout and stderr chunks
  are emitted as `output` events (data: stream, chunk, tool,
  step, task_id) WHILE the process runs, with `command`
  start/finish events around them (execute_step supplies the
  task/session/step context). The result contract is preserved:
  `{returncode, stdout, stderr}` with 8192-char tail caps, and
  the same ValueError semantics for not-found (404-class),
  timeout and OSError.
- **DEFERRED-NAMED — background `process` tool live output**: its
  output goes to a per-process log file read back by its `log`
  action, detached from any step context; callers poll it
  deliberately by design of that tool. Genuinely live output
  needs a reader-thread + log-cursor redesign of the process
  store. It was NOT faked by re-emitting the log on a timer.
  Boundary recorded here and in the `_process` code comment.

### Dashboard / monitor (push consumers)

- Dashboard: one-time history load from events.jsonl at startup,
  then `eventbus.EventSubscriber`; events are marshalled to the
  GTK loop with `GLib.idle_add`. The 500 ms event/takeover timers
  are gone (`poll_events` / `poll_takeover` removed). Bus-only
  signals never render as rows; takeover events refresh the
  banner (one re-count per event, not per tick).
- Monitor: one-time process/stop snapshot at startup, then
  refresh on relevant bus events (task/system/approval/settings/
  takeover/stop/command/output/agent/process) and on its own
  button actions. The 2000 ms timer is gone.
- Subscriber reconnect (in `EventSubscriber`) is subscription
  lifecycle — the daemon may start after the UI — not a display
  poll: no state is re-read on a timer.

### Task wake-up

- The daemon's 2-second task-queue scan is replaced: it blocks in
  `select` on (a) an inotify watch (`eventbus.DirectoryWatcher`,
  ctypes inotify, stdlib only) on the tasks directory and (b) a
  self-pipe written when the bus server accepts a task event.
  Cron and connector/channel cycles are preserved as timed
  cycles (30 s connector cycle; cron checked on every wake) —
  those are external services, not local state polling.
- **Documented safety fallback**: if inotify is unavailable, or a
  writer bypassed both the watch and the bus, the wait times out
  at `TASK_FALLBACK_SECONDS = 30` and the queue is rescanned.
  This is a fallback bound, not the normal freshness path (also
  stated in the daemon code).

### Host-bridge foundation (for M2–M4)

- `docs/BRIDGE-PROTOCOL.md` + `bridgeprotocol.py`: typed JSON
  envelopes (command/reply/error/event), request ids, hello +
  version negotiation, per-surface allowlists (host / settings /
  chat / dashboard at registry level), error shape, event
  forwarding shape, write-only secret handling (secret-bearing
  verbs only; replies/events strip secrets; `redact_params` for
  logs), and the hard rule: page content is untrusted, the host
  never evals page code and dispatches only allowlisted verbs.
  M1 defines/tests this core only — no WebView pages are built.

### P0 pickup note (explicitly NOT fixed here)

The bus is a freshness channel, **not** tamper-proofing: any
process in the dclawos-session group can connect and publish, so
bus events are unauthenticated and events.jsonl remains
group-writable shared state. Publisher authentication and
event-log trust (the external review's P0 item 3) must be
revisited by the later P0 hardening pass; M1 does not claim
otherwise and does not worsen the existing posture (socket/dir
modes match the existing exchange files). No other P0 item was
touched in this module (no ticket self-approval, stop-flag,
shell-tier, web_fetch SSRF, or systemd changes).

### Tests

- New headless gate `eventbustest.py`, run by hook 1008 in the
  image build and locally: bus start on a temp socket, subscriber
  delivery, durable log still written, malformed line + abrupt
  disconnect do not kill the server, bridge valid/invalid +
  non-allowlisted + secret-rule cases, live shell stdout/stderr
  chunks arriving before exit (print → 1.2 s sleep → print;
  first chunk must precede the run's return by >0.5 s; result
  contract and error/timeout messages intact), task wake via
  save_task bus signal + inotify watcher.
- Validate script: static assertions that eventbus /
  bridgeprotocol / eventbustest ship and compile, agentlib
  publishes, the daemon serves the bus and carries the inotify
  watcher, dashboard/monitor subscribe — plus negative checks on
  comment/docstring-stripped code: no `POLL_SECONDS` in the
  daemon, no `timeout_add` in dashboard/monitor, no
  `poll_takeover` in the dashboard; hook 1008 runs eventbustest.

## M2 — Model settings, complete (WebView page + full routing wiring)

State: **CLOSED** (this module; build verification in the ledger
row below).

### Storage (one registry, both sides read it)

- The unified registry (`registrylib`, `/var/lib/dclawos/agent-
  registry.json`) gains two M2 categories; `settingslib` is
  untouched (its 8 scalar keys and the P0-2 write hardening —
  dclaw-owned 0640 file in a 0710 directory, agent writes only via
  the root broker, denylist enforced in broker code — are exactly
  as before; no complex structure was forced into it):
  - `providers`: CUSTOM providers with full CRUD —
    `{id, name, baseUrl, format ∈ openai|passthrough|claude,
    models: [{id, name, pricing?}], enabled, description,
    license}`. Validation (registrylib.normalize_provider):
    id shape, http(s) baseUrl, format enum, unique model ids,
    pricing = finite numbers ≥ 0.
  - `model_config` (singleton): routing + pins + builtin
    overrides (shape and validation in
    registrylib.normalize_model_config; invalid input raises and
    is never persisted).
- Builtin providers are NEVER stored and cannot be deleted: the
  engine catalog (hook 1003, `dclawos-route --catalog`) is the
  source of truth. The CRUD-honest semantics for builtins are the
  overlay in `model_config.provider_overrides[<id>]` =
  `{enabled, baseUrl, addModels, hideModels}`; deleting the
  overlay = restore defaults. `registrylib.merged_providers`
  produces the single merged view the page and the runtime both
  consume.
- Readers/writers: the settings host (human, direct registry
  writes, same as the other registry categories) writes;
  `llmlib` (agent daemon) reads on every call; the daemon's
  `_subagent` reads `subagent_pin` when creating a child task.

### Wiring: page → host → registry → llmlib → engine

```
model-settings.html (WebView, untrusted)
  -- bridge commands --> dclawos-settings host (_model_dispatch)
  -- registrylib ------> agent-registry.json (providers/model_config)
  -- settingslib ------> agent-settings.json (global default only)
  -- secret.set --------> approval + broker write-secret (llm-<id>)
llmlib.build_request (per call, daemon side)
  = settings global default + model_config pins/routing/fallback/
    overrides + custom providers
  --> dclawos-llm stdin JSON --> llm.ts runLlm
      - routing fields forwarded to decideRouting (M2 pass-through
        added to LlmRequest/runLlm; decision.ts unchanged)
      - custom providers served as explicit targets +
        baseUrlOverrides; customPricing consulted by computeCost
        ahead of the vendored pricing table
```

Routing fields stored AND forwarded AND tested (modeltest fake-bin
capture): `strategy`, `routerStrategy` (both validated against
the engine's registered set rules/score/cost/eco/latency/fast/
sla-aware/sla/lkgp — `nadir` is NOT registered upstream and is
refused), `modePack` (ship-fast/cost-saver/quality-first/
offline-friendly/reliability-first/chaos-mode), `weights`
(16 factors; keys must be engine factor keys, every value finite
≥ 0, sum ≈ 1 ±0.01 — mirroring the engine's validateWeights;
unknown keys are refused because the engine's normaliser would
silently drop them), `budgetCap`, `budgetFallback`
(cheapest|strict), `explorationRate` [0,1], `sla`
({targetP95Ms, maxErrorRate, maxCostPer1MTokens,
hardConstraints} — the engine's SlaRoutingPolicy shape),
`lastKnownGoodProvider`, `maxTargets`, and the ordered `fallback`
target list (→ request `targets`, filtered to keyed + enabled
providers). With no model_config, the assembled request is
byte-for-byte the pre-M2 shape (asserted in modeltest).

Pins: selection order **explicit caller pin > task-type pin >
global default** (per-field). Task types are the engine's real
set — taskFitness FITNESS_TABLE: default, coding, review,
planning, analysis, debugging, documentation. There is NO `chat`
or `research` task type in the engine, so no pin slot was invented
for them. The subagent default pin applies at child-task creation
only when the child has no explicit pin. Compaction calls
(`_maybe_compact`) carry no task context, so they resolve through
the `default` pin level — unchanged from their pre-M2 behaviour
apart from that.

Custom/disabled semantics in llmlib: a custom pin never travels
as `provider=` (decideRouting would reject an unknown provider);
it is an explicit target. Disabled providers (custom enabled=false
or builtin override enabled=false) are excluded from assembled
targets. An explicit caller pin is always honoured.

### Health / status

- `dclawos-llm --status` (new, read-only) reuses the engine's own
  exports — `getQualitySnapshot`, `getProviderBreakerState` (per
  catalog provider), `getAllModelLockouts` (M2 re-exports added to
  llm.ts) — plus the persisted state files. `modellib` (host side)
  consumes it and falls back to reading the state files directly
  (`provider-quality.json`, `circuit-breakers.json`,
  `connection-rate-limits.json` under the engine state dir) when
  the binary is unavailable; empty sections are filled from the
  files so neither source hides the other.
- NAMED LIMIT: model lockouts live in the engine PROCESS memory
  (accountFallback). Engine calls are short-lived CLI processes,
  so lockouts are not observable across processes;
  `lockouts_available` is reported `false` with that explanation
  on the page and here — lockout data is never fabricated.
  Missing/corrupt state files surface as "无数据" per section.

### Usage ledger

- New `usagelib`: `/var/lib/dclawos/usage.jsonl`, one append per
  completed `llmlib.call` (provider, model, task_id, session,
  task_type, usage from the result's cost_envelope, cost_usd,
  attempts, ok). Aggregation (page + `model.usage` verb) sums
  calls/tokens and KNOWN cost; a null cost stays null and is
  counted separately as unknown-cost — never zero, never
  estimated. Corrupt lines are tolerated and counted.
- Recording starts with this version; earlier history has no
  per-call records and is not reconstructed (stated on the page).

### Connectivity test

- Bridge verb `model.test` → `llmlib.test_connection`: one
  minimal real request (short message, maxTokens 8) against
  EXACTLY the chosen provider/model (explicit single target, so
  fallback chains cannot mask the result). Returns the real
  outcome: ok, actually-served provider/model, attempts, latency,
  cost (null when unpriced), error summary. No key → returns
  `configured: false` with “未配置密钥”, sends nothing, fabricates
  no success. Gate coverage (modeltest, local mock only):
  success / 429-failure / no-key three paths.

### WebView page + host

- Page: `usr/share/dclawos/model-settings.html`, a single local
  file (offline, no external resources), loaded by the existing
  GTK3 `dclawos-settings` app in a WebKit2 4.1 WebView (package
  `gir1.2-webkit2-4.1` added to the desktop package list — the
  Bookworm 4.1/soup3 variant). The GTK4 shell migration remains
  M5; this module does not claim it. If WebKit2 is unavailable the
  panel degrades to an explanatory label and the native AI panel
  keeps working.
- The host registers every M2 verb in bridgeprotocol's settings
  surface FIRST, implements typed handlers (`_model_dispatch`),
  and the docs table (BRIDGE-PROTOCOL.md) lists them; the page
  only ever sends those verbs. Secret writes reuse the existing
  `secret.set` semantics and the app's approval + broker
  write-secret flow — no new echoable secret channel exists;
  replies/events are secret-stripped by bridgeprotocol as before.
- Refresh: the host subscribes to the M1 event bus and forwards
  settings/model/task/system events to the page (which re-reads
  the shared stores), and re-reads on window focus as before;
  the page refreshes itself after its own saves.
- UI rules on the page (native web form of docs/UI-RULES.md):
  executed verbatim — no borders, 1–3% lightness layers, the
  exact light/dark palettes, focus double-ring box-shadow with
  no outline, dark font-weight −40 (360/560), inline-icon
  vertical-align:-0.125em, tabular-nums, Grid 0fr→1fr folds,
  text-wrap:pretty, over-damped settle transitions +
  under-damped success pulse, feedback toast self-extinguishing
  at 2.5 s. Rule 4 (streaming blur reveal) governs chat text and
  has no surface on this page — it lands with M3's chat page;
  that is the only non-applicability, recorded here rather than
  faked.

### Tests

- New headless gate `modeltest.py`, run by hook 1008 and locally:
  registry validation refusals (bad weights/URL/format/SLA/pin),
  CRUD→assembly (custom target + baseUrl + pricing, builtin
  override endpoint, disabled excluded), pin order, full routing
  pass-through capture, connectivity success/failure/no-key
  against a local mock, usage aggregation with null-cost
  separation + corrupt-line tolerance, bridge allowlist
  (cross-surface + unregistered refusals, secret stripping),
  health tolerance for missing/corrupt state files.
- The engine side was additionally verified locally with a real
  esbuild bundle of the modified llm.ts: custom target executed
  against a local mock with custom pricing (cost priced=true),
  routing pass-through executed, --status exports callable.
- Validate script: M2 static block (libs/page/typelib presence +
  compile, page palette/no-external-resource assertions on
  comment-stripped text, llmlib wiring symbols, host/bridge/CLI
  wiring, hook 1008 registration).

### Named limits / not done

- Model lockouts cross-process: unavailable by construction
  (above), reported honestly, not faked.
- GTK/WebView visuals cannot be verified on this headless build
  machine; the page + host are gate-checked statically and the
  host degrades gracefully without WebKit2.
- Real-provider calls are unverified (no real keys exist in the
  build environment); everything above is proven against local
  mocks only.

## Module ledger

| Module | Commit | Run | Result |
|---|---|---|---|
| P6-M1 event bus + live output + task wake + bridge core | (this commit) | — | pending |
| P6-M2 model settings complete (WebView + routing wiring + usage ledger) | (this commit) | — | pending |

## Later modules (not started)

- M2 model settings WebView, M3 chat streaming WebView,
  M4 dashboard timeline WebView — all on the bridge core above.
- M5 GTK4 migration, M6 close-out.
