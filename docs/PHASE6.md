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


## M3 — Chat WebView + true streaming

State: **CLOSED** (this module; build verification in the ledger
row below).

### Engine true streaming (OpenAI-compatible path)

- `llm.ts` gains a stream mode (`LlmRequest.stream` + `onDelta`):
  for registry format `openai` / `passthrough` targets the new
  `executeStreamTarget` sends `stream:true` (with
  `stream_options.include_usage`), parses the SSE byte stream
  incrementally (`data:` lines, `[DONE]`, JSON fragments),
  forwards each content/tool fragment to `onDelta` AS IT
  ARRIVES, and assembles the same OpenAI-shaped body the
  non-streaming path returns (content, tool_calls assembled by
  fragment index, usage when the provider sends it — a provider
  that sends no usage yields cost null; usage is never invented).
- A provider that answers a stream request with a plain JSON body
  is assembled as-is and its text is surfaced as one STAGED delta
  (never labelled SSE).
- Attempt loop (`runLlmStreaming`): targets are score-ordered
  with dispatch's own helpers (`setupDispatch` / `scoreTargets` /
  `expandTargets` / `classifyFailure` — reused, not re-derived).
  Fallback semantics are FIXED: before the first delta, a failed
  target falls through to the next target; AFTER the first delta,
  a failure closes the result as an honest partial error
  (`partial: true`, `error` set, response null) — no other
  provider silently continues a half-written reply. The
  non-streaming `dispatch()` path is byte-for-byte the pre-M3
  behaviour apart from the additive envelope field.
- Result envelope: same shape as non-streaming (response /
  provider / model / attempts / fallbackCount / costUsd /
  cost_envelope) plus `stream_mode: "sse" | "staged"` and, on a
  post-delta failure, `partial` + `error`.
- Custom providers: the engine registry has no entry for them, so
  llmlib now forwards their registry format as `providerFormats`
  (consulted only for providers the vendored registry does not
  know); builtin formats still come from the registry itself.

### Per-format streaming status (honest table)

| Format | Path | stream_mode |
|---|---|---|
| openai | SSE (`stream:true`, incremental parse) | `"sse"` |
| passthrough | SSE (same executor) | `"sse"` |
| claude | non-streaming translator branch (llmClaude), whole reply surfaced as staged delta(s) | `"staged"` — 分阶段显现，非供应商流式 |
| any other registry format | fails closed as before (unsupported format) | `"staged"` on the (failed) envelope; no delta is emitted |

### CLI + llmlib

- `dclawos-llm --stream` (or `"stream":true` in the request):
  NDJSON on stdout — `{"type":"delta", text?, tool_calls?,
  stream_mode}` lines as fragments arrive, then exactly one
  `{"type":"result", ...envelope}` line. Keys still arrive only
  via `DCLAWOS_LLM_KEYS` and are never printed.
- `llmlib.call_stream(..., on_delta=...)`: same `build_request`
  assembly and the SAME `_record_usage` ledger write (exactly
  once per completed call) as `call()`. Non-streaming `call()`
  callers (route_decide, compaction, subagents) are unchanged.

### Daemon wiring

- `run_llm_task` now calls `llmlib.call_stream`. `on_delta`
  publishes fragments as `llm` bus events via
  `agentlib.log_event` (durable log first, then bus) with data
  `{delta, stream_mode, conv_session, turn, seq, task_id}`.
  Coalescing: fragments accumulate until **48 chars** are
  buffered OR **0.25 s** elapsed since the last publish; the
  remainder flushes when the call returns. (Constants
  `STREAM_COALESCE_CHARS` / `STREAM_COALESCE_SECONDS` in
  dclawos-agent.)
- Tool-call turns still run through `execute_step`; command /
  output events (M1) are what the chat page renders as inline
  step cards. A partial stream closes the task as failed with
  `result.partial=true` and an error stating the interruption;
  the final task result carries `stream_mode`.

### Approval cards (display + verdict wiring only)

- `agentlib.request_approval` now publishes its approval events
  with data `{ticket_id, tool, tier, summary, params}` where
  params are redacted via `bridgeprotocol.redact_params` BEFORE
  the event is written or published (ticket files still hold
  only the params hash + summary — ticketlib is unchanged).
  Tier comes from `agentlib.classify` (for the broker
  `set-setting` operation name it reports tier 2, matching
  settings_set's tier).
- The chat host renders the card from the event data + the
  ticket's operation/summary, and decides it through
  chatlib's field-for-field copy of the dclawos-approve write
  (status / approved_by="human-gui" / approved_at /
  ticketlib.save_ticket). There is no second verdict channel,
  and the ticket trust model (self-approval / tamper-proofing)
  is NOT touched — that is the later P0 pass, as in M1.

### Chat WebView page + host

- `dclawos-chat` is now a WebKit2 4.1 host in the M2 settings
  host pattern (typed bridge dispatch, bus subscription
  forwarding, focus re-read; WebKit missing → explanatory label,
  no second UI). All logic lives in the new headless
  `chatlib.ChatHost`; the GTK4 shell migration stays M5.
- Page: `usr/share/dclawos/chat.html`, single local file, no
  external resources. Sessions list + switch + new (convlib),
  history rendering on switch, streaming bubbles (rule-4 reveal
  on new text nodes only; animation decoupled from arrival),
  final-reply reconciliation by replacement (no duplicate final
  text), inline tool step cards from command/output events,
  inline approval cards, stop button (existing cooperative stop
  via dclawos-agent-ctl), slash commands (host-side
  parse_command, transplanted pre-M3 semantics), assign-task
  Grid fold form, header model switcher (merged catalog,
  settingslib global default), honest unconfigured banner, and
  per-reply copy buttons via the host clipboard with the 2.5 s
  check feedback.
- Event filtering is HOST-side (chatlib.handle_bus_event): only
  events whose task_id / task session / conversation belongs to
  this window are forwarded; task completion is reconciled from
  ONE event-driven task-file re-read per relevant event and
  yields exactly one `chat.task_done` page event.

### UI rules on the chat page (deviations named)

Rules 1, 2, 3, 5, 6, 7, 8, 9, 10 are executed verbatim in native
web form (palette tokens, double-ring focus, dark weights
360/560, inline-icon -0.125em, tabular-nums, Grid 0fr→1fr,
text-wrap:pretty, over/under-damped motion tokens, 2.5 s
self-extinguishing feedback). Rule 4 is now native on this page
(real CSS `blur(6px)` → clear, 90 ms, binding easing, 30 ms
stagger cycled every 24 chars so long deltas start immediately)
— the GTK alpha-ramp deviation recorded in UI-RULES.md no
longer applies TO THE CHAT PAGE (it still applies to the
remaining GTK3 surfaces until M5). No other deviation.

### Bridge verbs (chat surface, M3)

`chat.state`, `chat.send`, `chat.history`, `chat.assign`,
`chat.switch`, `chat.new`, `chat.model.set`, `chat.approve`,
`chat.deny`, `chat.stop`, `chat.copy`, `chat.launch` — registered
in bridgeprotocol first, implemented in chatlib, documented in
docs/BRIDGE-PROTOCOL.md, called by the page last.

### Tests

- New headless gate `chattest.py`, run by hook 1008 and locally:
  (a) real `dclawos-llm --stream` against a local mock SSE
  server with delayed chunks — first delta arrives >0.5 s before
  the result line, delta assembly equals the final reply, usage
  carried, stream_mode "sse", cost priced from mock usage
  (gpt-4o-mini mock numbers → 0.00045); a no-usage stream keeps
  cost null; (b) pre-delta 500 → honest null + exactly one
  attempt; no keys → llmlib sends nothing and returns a null;
  (c) real daemon `run_llm_task` over a temp bus — llm delta
  events arrive in seq order and assemble to the reply, the
  usage ledger gains exactly one record with null cost on the
  no-usage endpoint; (d) claude target → delta + result both
  labelled "staged", never "sse"; (e) ticketlib ticket decided
  via the host's approve/deny — status/approved_by/approved_at
  match dclawos-approve; secret-shaped params never appear in
  the serialised chat.state payload (redacted marker present)
  nor in agentlib's published approval event; (f) parse_command
  step structures for /run, /write, /mcp, /subagent identical to
  the pre-M3 window. Plus bridge allowlist (M3 verbs refused on
  other surfaces) and host send/model/session verbs.
- Validate script: M3 static block (chatlib/chattest ship +
  compile, chat page palette/rule-4/no-external assertions on
  stripped text, host EventSubscriber/WebKit2 wiring, negative
  stripped-code checks: no `timeout_add`, no `events_path` in
  dclawos-chat; llmlib `call_stream`, CLI `--stream`, engine
  `executeStreamTarget`/`stream_mode`, bridge chat verbs, hook
  1008 registration). The two Phase 5 chat assertions that
  pointed at the old GTK window (stream_reveal / copy label)
  now point at the chat page, where those behaviours live.
- Local runs before push: chattest, modeltest, settingstest,
  agentlooptest and eventbustest pass (eventbustest's initial
  subscribe race flaked once locally and passed on retry — the
  same race exists on the base commit; agentlooptest needs the
  image layout under /usr/local, provided locally via symlinks).

### Correction (2026-10-31, post-M3 incident)

The first version of chattest's no-key section called
`llmlib.call_stream` with an explicit provider/model pin and NO
base-url override, asserting only that the LOCAL mock saw no
hit. That assertion was blind: `call_stream` had no no-key
short-circuit, and the engine pushes an explicit pin as a target
even keyless, so the call attempted the provider's DEFAULT
endpoint unauthenticated (this triggered a Muse permission
card at 10:27 CST; the full accounting is in the M3 worker's
report to the coordinator). Fixes, in this follow-up commit:
`llmlib.call_stream` now short-circuits before spawning the CLI
when NO provider key exists at all (the M2 `test_connection`
rule; honest empty envelope with `configured: false`, nothing
sent, nothing recorded), and the chattest section sets the
local-mock override as a backstop and asserts zero attempts, so
a regression lands on the mock and fails the gate instead of
reaching a real endpoint.

### M3.1 — same short-circuit for the non-streaming call()

The pre-M3 `llmlib.call` carried the same hazard in library
form: no no-key short-circuit, with only caller-side guards
keeping it from ever firing. `call()` and `call_stream()` now
share ONE helper (`llmlib._short_circuit`, one honest envelope
with `configured: false`, empty attempts, null cost): a call is
not attempted when (a) no provider key exists at all, or (b)
the caller pinned a provider explicitly and that provider
holds no key — the engine pushes an explicit pin as a target
even keyless, so a keyless pin must never carry a user prompt
to an unconfigured provider's default endpoint. Auto routing
with at least one key present is unchanged, as is every keyed
path (daemon compaction, test_connection keeps its own
pre-check and is unaffected). Short-circuited calls spawn no
CLI and write no usage-ledger record. Gate evidence:
modeltest section 9 asserts the fake binary's invocation count
and the ledger line count move by ZERO across the zero-key
pin, zero-key auto-route and keyless-pin cases (with the local
mock override in place as backstop), and by exactly ONE each
for a keyed call; chattest / eventbustest / settingstest /
agentlooptest pass unchanged.

### Named limits / not done

- WebView visuals and the page's JS runtime cannot be verified
  on this headless build machine: the page is gate-checked
  statically (palette/rule tokens/no external resources) and
  every host behaviour behind it is covered by chattest; the
  in-page rendering/animation itself is NOT verified.
- Real-provider streaming is unverified (no real keys exist in
  the build environment; per the standing rule all tests use
  local mocks only). SSE parsing is proven against the mock's
  OpenAI-shaped chunks, including usage-in-final-chunk.
- Approval events are a display/verdict wiring only: publisher
  authentication and ticket tamper-proofing remain the later
  P0 pass (unchanged from M1's note).
- The `process` tool's live output remains the M1
  DEFERRED-NAMED item; step cards show shell steps' live output.

## M4 — Dashboard WebView + taskbar agent state + approval overlay

State: **CLOSED** (this module; build verification in the ledger
row below).

### Dashboard WebView (page state)

- `dclawos-dashboard` is now a WebKit2 4.1 host in the M2/M3
  pattern (typed bridge dispatch, bus subscription forwarding,
  focus re-read; WebKit missing → explanatory label, no second
  UI). All logic lives in the new headless `dashboardlib
  .DashboardHost`; the GTK4 shell migration stays M5. The binary
  name and launch entry are unchanged, and the startup
  "dashboard started" event write is kept.
- Page: `usr/share/dclawos/dashboard.html`, a single local file,
  no external resources. It carries everything the GTK dashboard
  showed, with the same semantics: the durable events file is
  read ONCE at host construction for history; after that the
  timeline is bus events only (no display timer anywhere, the
  file is never re-read).
- Snapshot/live seam: the host seeds an `EventDeduper` with the
  history envelope ids; a live event already in the snapshot is
  dropped, a new one is appended exactly once
  (`dashboardlib.merge_events` is the pure form, gate-tested).
  Id-less legacy events dedupe on a (ts, type, summary, session,
  task_id) fingerprint. The page dedupes again by the same key
  as a second line of defence.
- Counts area: running / waiting (queued) / done / failed task
  counts from `agentlib.list_tasks`, plus token totals and cost
  from `usagelib.aggregate_usage` — known cost summed, null-cost
  calls counted separately as unknown and NEVER rendered as 0.
  Counts are recomputed BY THE HOST when a relevant bus event
  arrives and pushed as `dashboard.counts`; neither the page nor
  the host polls on a timer.
- Takeover banner: a Grid-fold banner on the page, shown when
  `takeoverlib` has pending requests; clicking it launches
  `dclawos-takeover` via `dashboard.launch` (the pre-M4 banner's
  launch path, kept). Its count is seeded once from the store
  and afterwards maintained from bus takeover events.

### Taskbar agent state (four-state reduction table)

`dclawos-shell` gains ONE element — an agent-state indicator
(dot + label) — and a bus subscription. Slot geometry, the 48px
height (`TASKBAR_HEIGHT = 48`) and the `_NET_WM_STRUT_PARTIAL`
reservation are byte-untouched (the shell diff is additions
only; the validator asserts both). Clicking the indicator opens
the dashboard through the existing slot machinery.

The state is `dashboardlib`'s pure reducer (shared with the
dashboard host, so the two surfaces cannot disagree). It is
seeded ONCE from the stores (`initial_shell_state`: pending
ticket ids, pending takeover ids, running task ids) and after
that moves ONLY by reducing bus events — no store is re-read
periodically. Priority is by set, and clearing the top set falls
back to the next level, never past a still-pending one:

| Priority | State | Label | Enters when | Clears when |
|---|---|---|---|---|
| 1 (highest) | attention | 需要你 | a takeover request is pending (takeover event, status pending) | that request resolves (done / failed / cancelled) |
| 2 | approval | 等待批准 | an approval is pending (approval event with a ticket_id and no verdict) | the verdict event (approved / denied) for that ticket |
| 3 | thinking | 思考中 | a task is active (task signal queued/running, or llm / command / output activity for a task/session) | the task's terminal signal (done / failed / cancelled) or terminal system event |
| 4 | idle | 空闲 | none of the above | — |

The waiting states (approval / attention) breathe: a GLib timer
toggles ONLY the indicator's opacity (animation frames). It
reads no data and no files — recorded here because the timer's
presence must not be mistaken for a poll.

### Approval overlay (scope + the scoped-approval queue record)

- The overlay is a modal card ON THE DASHBOARD PAGE. When an
  approval event arrives, the host builds the card from the
  event's structured data via the shared
  `chatlib.approval_card_from_event` / `approval_card_view`:
  real tool name, tier, summary and params REDACTED host-side
  (`redact_params`) before they cross the bridge — never parsed
  out of the summary text. Cards seeded from the ticket store at
  startup carry the ticket's operation + params_summary and
  params `{}` — ticket files hold only the params hash and
  summary (ticketlib contract), so no raw params exist to show;
  none are fabricated.
- The card shows a risk band coloured by tier and exactly TWO
  buttons, 批准 / 拒绝, decided via `dashboard.approve` /
  `dashboard.deny` → `chatlib.apply_verdict` — the verdict write
  EXTRACTED in M4 from the chat host into one shared function
  (status / approved_by="human-gui" / approved_at /
  ticketlib.save_ticket, field-for-field dclawos-approve). The
  chat host now delegates to the same function; there is no
  third copy and no second verdict channel. After a verdict the
  overlay closes and the timeline, counts and taskbar state all
  update from the same event flow.
- **Scoped approval — QUEUED, not shipped (honesty record).**
  A third button ("本次任务允许" / approve-for-this-task) was
  evaluated and NOT built: the ticket model is per-operation
  single-ticket (`ticketlib.new_ticket(operation, params,
  summary, ttl)` binds ONE operation + params hash; the broker
  validates exactly that), and there is NO task-level
  authorisation carrier anywhere in ticketlib / agentlib /
  the broker to honour such a grant. Faking the button would
  promise a grant nothing enforces. "范围授权 (scoped
  approval)" is therefore recorded here as a still-queued item
  for the P0 / usability stage — it needs a ticket-model
  extension (scoped grant record + broker/agentlib honouring
  it) before any UI may offer it. (This record lives in this
  ledger; M6 note: at M4 time the repo had no
  docs/UI-FUNCTIONS.md — M5 created that file and carries the
  same queue record as a 未实现-已排队 row there, so the record
  now lives in both places.)

### Bridge verbs (dashboard surface, M4)

`dashboard.state`, `dashboard.approve`, `dashboard.deny`,
`dashboard.launch` — registered in bridgeprotocol first (they
replace the never-implemented M1 placeholders
`dashboard.snapshot` / `takeover.open` / `event.ack`),
implemented in dashboardlib, documented in
docs/BRIDGE-PROTOCOL.md, called by the page last.

### UI rules on the dashboard page (deviations named)

Rules 1, 2, 3, 5, 6, 7, 8, 9, 10 are executed verbatim in native
web form (palette tokens, double-ring focus, dark weights
360/560, inline-icon -0.125em, tabular-nums, Grid 0fr→1fr fold
for the takeover banner, text-wrap:pretty, over/under-damped
motion tokens, 2.5 s self-extinguishing toast). Rule 4 applies
to the page's llm rows: streamed delta text reveals with the
native 90 ms blur(6px) keyframe and 30 ms stagger (cycled every
24 chars); non-streamed rows render plainly, which is the
rule's honest scope (it governs streaming text, as on the M3
chat page). No other deviation.

### Tests

- New headless gate `dashboardtest.py`, run by hook 1008 and
  locally: (a) snapshot construction from fixtures — task
  counts by status, usage aggregation with the null-cost split,
  history order, and the snapshot/live seam (merge_events +
  host dedupe by envelope id, duplicate dropped, new event
  admitted exactly once); (b) the shell reducer's full
  transition chain idle → thinking → approval → attention →
  level-by-level fallback, re-delivery idempotence,
  auto-approved events never pending, input never mutated;
  (c) the overlay payload — a secret-shaped param never appears
  in clear text in the serialised host payload, tool name and
  non-sensitive params stay visible, store-seeded cards carry
  params {} honestly, and dashboard.approve/deny write exactly
  the dclawos-approve fields; (d) pending-count seeding from
  the stores plus event-only increments walking the state back
  down to idle. Plus the bridge allowlist (M4 verbs refused on
  other surfaces; retired placeholders refused) and
  dashboard.launch targets. Gate-first evidence: with the
  reducer's priority order deliberately swapped, dashboardtest
  fails ("pending approval did not outrank thinking"); restored,
  it passes — one side changed, same artifact re-run.
- Validate script: M4 static block (dashboardlib/dashboardtest
  ship + compile, page palette/rule/no-external assertions on
  comment-stripped text, host WebKit2 wiring + a stripped-code
  no-`timeout_add` check, bridge dashboard verbs, hook 1008
  registration) plus taskbar geometry guards
  (`TASKBAR_HEIGHT = 48` and `_NET_WM_STRUT_PARTIAL` still
  present, reducer wiring present). The three pre-M4 assertions
  that pointed at the old GTK dashboard (takeover banner /
  monitor button / editor button in the binary) now point at
  their M4 homes (dashboard page + dashboardlib launch table),
  the same treatment M3 gave the Phase 5 chat assertions.
- Local runs before push: dashboardtest, eventbustest,
  modeltest, chattest and settingstest pass (chattest/modeltest
  against the locally built engine bundle + local mocks only,
  as in M3).

### Named limits / not done

- WebView visuals and the page's JS runtime cannot be verified
  on this headless build machine: the page is gate-checked
  statically (palette/rule tokens/no external resources) and
  every host behaviour behind it is covered by dashboardtest;
  the in-page rendering/animation itself is NOT verified. The
  taskbar indicator is likewise GTK-visual-unverified here.
- Scoped approval is queued, NOT shipped (record above); the
  overlay honestly offers approve/deny only.
- Approval events remain a display/verdict wiring only:
  publisher authentication and ticket tamper-proofing remain
  the later P0 pass (unchanged from M1/M3).

## Module ledger

Superseded at close-out: the per-module rows originally kept
here (all "pending") are replaced by the verified
**Phase 6 总台账** in the M6 close-out section at the end of
this file (commits / runs / releases / ISO bytes, all checked
against the GitHub API and the release assets at M6). The
module sections above and below remain the technical record.

## M5 — GTK4 + libadwaita migration & function-map gate

State: **CLOSED** (this module; build verification in the ledger
row below).

### Gate-first document

`docs/UI-FUNCTIONS.md` was written BEFORE any window was touched:
the full function inventory assembled from repo evidence only
(PHASE4-GAP / PHASE5 / this file's M1–M4 ledgers, bin docstrings,
settingslib KEY_SPECS — 8 keys row by row, registrylib's six
categories, toolslib's 27 tool schemas one row each, the four
bridge surfaces, the broker secret store). Every row has a
landing point and a status from the document's own vocabulary
（已映射-GTK4 / 已映射-WebView / GTK3例外-Wnck / 不适用-非GTK /
未实现-已排队）. The scoped-approval queue record from M4 has its
own 未实现-已排队 row (landing point: P0/usability stage), as do
WhatsApp (BLOCKED-NAMED in Phase 5) and IRC/Mattermost
(deferred in Phase 5). No row was deleted to fake coverage.

### apt verification (Bookworm — the image's distro)

Verified on packages.debian.org/bookworm on 2026-10-31 (the
workstation's own apt is Ubuntu and was NOT used as evidence):

| Package | Bookworm | Verdict |
|---|---|---|
| gir1.2-gtk-4.0 | 4.8.3+ds-2+deb12u1 | exists |
| gir1.2-adw-1 | 1.2.2-1 (libadwaita-1-0 ≥ 1.2~beta) | exists |
| gir1.2-gtksource-5 | 5.6.x series | exists |
| gir1.2-webkit-6.0 | 2.50.6-1~deb12u2 (bookworm-security; deps gir1.2-gtk-4.0 + libwebkitgtk-6.0-4) | **exists → hosts migrate for real, NO exception** |

Code was written to the Bookworm API generation (GTK 4.8 /
Adw 1.2 / GtkSource 5.6 / WebKitGTK 6.0): no Gtk.FileDialog /
Gtk.AlertDialog (4.10+), no Adw.ToolbarView /
Adw.NavigationSplitView (1.4+). Dialogs use the GTK4-retained
Gtk.Dialog / FileChooserDialog / MessageDialog with the
`response` signal + a nested GLib main loop (`run_dialog()`,
one helper per window — GTK4 removed `.run()`); file choosers
use `get_file().get_path()` (GTK4 removed `get_filename()`);
main loops are GLib.MainLoop on `close-request` (GTK4 removed
Gtk.main() / the destroy signal); focus refresh uses
Gtk.EventControllerFocus (GTK4 removed focus-in-event).

### Per-window conclusions

| Window | Conclusion |
|---|---|
| dclawos-settings | Migrated: Adw.ApplicationWindow shell (Adw windows reject set_titlebar — it traps; the Gtk.HeaderBar is the content box's first child, Stack+StackSidebar switching unchanged), all six panels function-preserved, model page host on WebKitGTK 6.0 (`WebKit.WebView(user_content_manager=...)` — the 4.1-era `new_with_user_content_manager` classmethod does not exist in the 6.0 binding) |
| dclawos-editor | Migrated: GTK4 + GtkSourceView 5. Real API deltas fixed: SearchContext.forward/backward slice (5 returns a wrapped flag), buffer get_can_undo/get_can_redo (renamed), TextBuffer get_iter_at_* tuple unwrap. EDITOR-DECISION functions all preserved (open/save/find/replace/goto/undo/redo/terminal/compare/sidebar) |
| dclawos-monitor | Migrated: GTK4 + Adw.init; stop/resume/kill + bus-push refresh unchanged; kill confirm via run_dialog |
| dclawos-takeover | Migrated: GTK4 + Adw.init; Q2 backfill semantics untouched; its 1 s list poll is pre-existing (M1 push did not cover this window) and is recorded as-is in UI-FUNCTIONS, not hidden |
| dclawos-approve | Migrated (kept): still a live fallback entry — dashboardlib LAUNCH_TARGETS maps approve→dclawos-approve and secret-save approvals are decided by whoever opens it; verdict write fields unchanged. NOT deleted, NOT declared superseded |
| dclawos-chat | Host migrated: GTK4 + WebKitGTK 6.0; page + chatlib unchanged; clipboard moved to Gdk 4 (`Display.get_clipboard().set(text)`; Gtk.Clipboard is gone) |
| dclawos-dashboard | Host migrated: GTK4 + WebKitGTK 6.0; page + dashboardlib unchanged |
| dclawos-shell | **GTK3 EXCEPTION (named)**: Wnck (gir1.2-wnck-3.0) is GTK3-only with no GTK4 equivalent and window switching/activation depends on it; geometry (48px, _NET_WM_STRUT_PARTIAL) byte-untouched. One M5 change inside it: an explicit Gdk 3.0 pin (below) |
| dclawos-gui | Not a window (Wnck CLI): unchanged except the same Gdk/Gtk 3.0 coexistence pin (below) |
| dclawos-secret / dclawos-installer / other CLIs & daemons | 不适用-非GTK (registered in UI-FUNCTIONS), untouched |

### Coexistence bug found & fixed by the migration (real, proven)

With both GTK generations' typelibs in one image, an unpinned
`Gdk` import resolves to Gdk 4.0 FIRST, after which Gtk 3.0
refuses to load ("Requiring namespace 'Gdk' version '3.0', but
'4.0' is already loaded") — the taskbar and dclawos-gui would
have died on launch in the M5 image. Proven locally with both
typelib generations installed (the pre-M5 shell fails exactly
this way; with `gi.require_version("Gdk", "3.0")` pinned before
the import, the taskbar constructs and `dclawos-gui windows`
works). Both files carry the pin with a comment. theme.py
likewise no longer pins Gtk 3.0 itself: it branches on the major
version the app process already loaded.

### theme / UI-RULES

theme.py is dual-generation (GTK4: theme-gtk4.css /
theme-gtk4-dark.css via add_provider_for_display; GTK3:
original sheets via add_provider_for_screen; helpers
apply_prose / make_section / stream_reveal branch on the loaded
major version — GTK4 renames: Label set_wrap, Revealer
set_child, flat class instead of ReliefStyle, iter tuples).
Palette tokens are identical across generations (validate M5
block asserts parity + no #000 in all sheets). docs/UI-RULES.md
gained an additive M5 addendum; no rule was weakened.

### Package list & expected ISO impact

Removed: gir1.2-webkit2-4.1, gir1.2-gtksource-4. Added:
gir1.2-gtk-4.0, gir1.2-adw-1, libadwaita-1-0, gir1.2-webkit-6.0,
gir1.2-gtksource-5. Kept: gir1.2-gtk-3.0 + gir1.2-wnck-3.0
(taskbar exception, dclawos-gui, LightDM greeter). WebKitGTK
6.0 replaces a same-sized WebKitGTK 4.1; GtkSource 5 replaces 4;
the net add is the GTK4+libadwaita stack alongside GTK3 —
estimated +20–50 MB on the ISO (estimate only; the ledger row
records the measured figure).

### Gates changed, with reasons (verify-the-gate-first)

- validate: GtkSource-4.typelib → GtkSource-5.typelib;
  WebKit2-4.1 package/typelib assertions → WebKit 6.0; the three
  host greps 'WebKit2' → '"WebKit", "6.0"'. Reason (M3/M4
  precedent): these assertions pointed at the M2-era host
  implementation; M5 moved the hosts, so the assertions now
  point at the functions' new home. The assertions' INTENT
  (a WebKit typelib ships; each host loads it) is unchanged.
- validate: new M5 block — UI-FUNCTIONS existence + status
  vocabulary + no residual unmapped table rows; per-window
  Gtk 4.0/Adw pins and Gtk 3.0/WebKit2 absence in the final
  image; editor GtkSource 5 pin; settings Adw.ApplicationWindow;
  the Gtk-3.0 exception set across ALL bins equals exactly
  {dclawos-gui, dclawos-shell}; the shell's Gdk 3.0 pin; package
  list presence/absence; Gtk-4.0/Adw-1/GtkSource-5/WebKit-6.0
  typelibs in the image; GTK4 theme sheets + token parity.
- No existing test was weakened: hook 1008 is untouched and all
  eight suites pass locally (below).

### Local verification (headless, before push)

The build VM is Ubuntu, not Bookworm — its GTK 4.14/Adw 1.5/
GtkSource 5.12/WebKit 6 typelibs were installed ONLY as a
construction-smoke environment (newer than Bookworm's; code
avoids post-Bookworm APIs per the table above). Under Xvfb:
settings (all six panels incl. the WebKit6 model page),
chat host, dashboard host, editor (ctor + find + undo +
compare window + compare dialog), monitor, takeover, approve
all construct and present; the GTK3 taskbar constructs in the
dual-typelib environment (after the Gdk pin); dclawos-gui runs.
WebKit script-message delivery + evaluate_javascript +
Gdk clipboard were exercised directly against WebKit 6.
Suites: settingstest / eventbustest / dashboardtest /
modeltest / chattest / agentlooptest / channeltest /
provisiontest all pass (channeltest initially failed locally
only because the workstation lacks the image's
python3-websocket package; with it installed, it passes —
environment gap, channel code untouched by M5).
Parity note: theme.py's GTK3 apply_theme returns False under
the workstation's GTK 3.24 for a pre-existing CSS parse
rejection (font-feature-settings) — the BASE commit's theme.py
returns False identically on the same machine (same artifact,
both versions), so this is not an M5 regression; the GTK4
branch returns True.

### Named limits / not verified

- Bookworm-exact runtime (GTK 4.8/Adw 1.2/GtkSource 5.6/
  WebKitGTK 6.0-2.50) was not executed: the local smoke used
  Ubuntu's newer stack, and CI is headless (hook 1008 +
  validate are static/logic gates). Window construction on
  the Bookworm generation and all GTK visuals remain
  on-device verification items, as in every prior phase.
- The WebView pages' JS runtime is unchanged by M5 and remains
  as (un)verified as M2–M4 recorded; the host bridge path was
  re-proven against WebKit 6 locally (message in, JS out).
- dclawos-takeover's 1 s poll remains: converting it to bus
  push is a behaviour change outside M5's migration mandate
  and is recorded in UI-FUNCTIONS rather than silently kept.

### M5 gate fix (run #57 failure, for the record)

Run 41167828374 (#57) built the ISO successfully (all hooks,
including the 1008 suites inside the Bookworm chroot with the
new GTK4/WebKit6 packages, passed) but the Verify step failed
at validate line 1156 with exit 2 and no assertion message:
the M5 block's own `grep -l ... usr/local/bin/*` hit the
`__pycache__` DIRECTORY in the extracted bin dir; grep exits 2
on that error even when matches are found, and `pipefail`
turned the command-substitution assignment into a fatal
failure before the comparison could print anything. Gate-side
bug in the new M5 assertion itself (verify-the-gate-first:
the per-window pins and every earlier M5 assertion had already
passed against the real ISO); product code unchanged. Fix:
`grep -d skip` so directories in the glob are skipped, in this
follow-up commit — the P4-M4 gate-fix precedent.
Gate-fix addendum (run #58): the `-d skip` form still exited 2
silently at the same line on the CI runner (it passes locally;
the extracted bin dir on the runner evidently contains an entry
the grep pipeline chokes on). The check was rewritten in
python (skip non-files, tolerate binaries, print the collected
set verbatim on mismatch, `||` fallback so a collector failure
becomes a readable mismatch instead of a silent exit 2). The
assertion's intent and expected set are unchanged; the literal
script snippet was executed locally against the source tree
and collects exactly `dclawos-gui dclawos-shell`.

## M6 — Close-out （关账）

State: **CLOSED**. Documentation close-out only: this module
changes no product code. Every number below was re-checked at
M6 against the GitHub API (workflow runs) and the release
assets (ISO byte counts) — not copied from the per-module
draft rows.

### Phase 6 总台账 (master ledger)

Baseline before Phase 6: commit 5ee669c, run 41153538403
(#50) success, release dclawos-2.0-preview-50, ISO
1,060,083,712 bytes.

| Module | Commit(s) | Run | Conclusion | Release | ISO (bytes) |
|---|---|---|---|---|---|
| M1 event bus + live output + task wake + bridge core | b8d00c3 | 41155565705 (#51) success | CLOSED | dclawos-2.0-preview-51 | 1,060,132,864 |
| M2 model settings complete (WebView + routing wiring + usage ledger) | 8243252 | 41157940464 (#52) success | CLOSED | dclawos-2.0-preview-52 | 1,138,573,312 |
| M3 chat WebView + true streaming | f2fb68f | 41160534403 (#53) success | CLOSED | dclawos-2.0-preview-53 | 1,138,630,656 |
| M3 correction (no-key short-circuit in call_stream; 10:27 incident fix — see the archive line below) | b6e9b3a | 41162106261 (#54) success | CLOSED | dclawos-2.0-preview-54 | 1,138,630,656 |
| M3.1 same short-circuit for the non-streaming call() | 1bb33f7 | 41163678253 (#55) success | CLOSED | dclawos-2.0-preview-55 | 1,138,636,800 |
| M4 dashboard WebView + taskbar agent state + approval overlay | 4a30c86 | 41165377262 (#56) success | CLOSED | dclawos-2.0-preview-56 | 1,138,657,280 |
| M5 GTK4+libadwaita migration + UI-FUNCTIONS gate | fe466c1 + gate fixes 5137c32 / fa0378b | 41167828374 (#57) FAILURE → 41168536796 (#58) FAILURE → 41169268309 (#59) success | CLOSED — the two red runs were gate-side bugs in the new M5 validate assertion itself (ISO built, all hooks passed; verify step died on the assertion — full record in the M5 section's gate-fix notes); no release exists for #57/#58 | dclawos-2.0-preview-59 | 1,145,554,944 |
| M6 close-out (this section; docs only) | (this commit) | verified after push; see the M6 report | CLOSED | (the release cut from this commit) | docs-only commit; any byte drift is build nondeterminism, recorded in the M6 report |

### ISO increment accounting (#51 → #59)

Total: 1,060,132,864 (#51) → 1,145,554,944 (#59) =
**+85,422,080 bytes (≈81.5 MiB)**. Step by step, each figure
the difference of the two adjacent release assets:

| Step | Delta (bytes) | Attribution |
|---|---|---|
| #50 → #51 (M1) | +49,152 | code-level (eventbus / bridgeprotocol / libs) |
| #51 → #52 (M2) | +78,440,448 | the bulk: WebKitGTK 4.1 (gir1.2-webkit2-4.1) enters the image for the first WebView host, plus the model page / libs |
| #52 → #53 (M3) | +57,344 | code-level (chat page / chatlib / engine streaming) |
| #53 → #54 (M3 correction) | 0 | llmlib/chattest fix; ISO size byte-identical |
| #54 → #55 (M3.1) | +6,144 | code-level |
| #55 → #56 (M4) | +20,480 | code-level (dashboard page / dashboardlib) |
| #56 → #59 (M5) | +6,897,664 | net of the stack swap: WebKitGTK 6.0 + GTK4 + libadwaita + GtkSourceView 5 in, WebKitGTK 4.1 + GtkSourceView 4 out, GTK3 kept alongside for the taskbar exception |

Sum check: 78,440,448 + 57,344 + 0 + 6,144 + 20,480 +
6,897,664 = 85,422,080 ✓ (the #50 → #51 step sits before the
#51 starting line and is listed only for completeness).

### Per-page / per-window final status

| 页面/窗口 | 形态 | 功能状态 |
|---|---|---|
| 模型设置页 (model-settings.html) | WebView（宿主 dclawos-settings，GTK4 + WebKitGTK 6.0） | 完整 — 供应商 CRUD/覆盖层、路由全字段、健康、连通性测试、用量账本（M2）；lockout 跨进程不可得如实报告，不编造 |
| 聊天页 (chat.html) | WebView（宿主 dclawos-chat，GTK4 + WebKitGTK 6.0） | 完整 — 会话/历史、真流式气泡、工具步骤卡、内联审批卡、停止、斜杠命令、指派表单、模型切换（M3） |
| 看板页 (dashboard.html) | WebView（宿主 dclawos-dashboard，GTK4 + WebKitGTK 6.0） | 完整 — 事件时间线（启动读一次历史、其后纯推送）、计数区、接管横幅、审批覆盖层（M4）；范围授权未建（见排队清单） |
| 设置外壳 (dclawos-settings 其余 5 页+AI 原生页） | GTK4 + libadwaita (Adw.ApplicationWindow) | 完整 — 通用/AI与模型/Skills与MCP/渠道/密钥六页功能逐项保留（M5，清单见 UI-FUNCTIONS.md） |
| 编辑器 (dclawos-editor) | GTK4 + GtkSourceView 5 | 完整 — 打开/保存/查找/替换/跳行/撤销重做/终端/diff 对比窗/agent 修改侧栏逐项保留（M5） |
| 监视 (dclawos-monitor) | GTK4 + libadwaita | 完整 — 进程总览 + 叫停/恢复/强杀；新鲜度为 M1 事件总线推送，无显示定时器 |
| 接管 (dclawos-takeover) | GTK4 + libadwaita | 完整，例外说明 — Q2 回填语义不变；其请求列表保留 1s 定时刷新（POLL_MS=1000，M1 推送化未覆盖此窗，UI-FUNCTIONS.md 已登记），推送化不在 Phase 6 授权内 |
| 审批窗 (dclawos-approve，独立窗） | GTK4 + libadwaita | 完整 — 仍在用的兜底审批入口（看板覆盖层/聊天内联卡与它共用同一 verdict 写路径 chatlib.apply_verdict）；例外说明：其票据列表保留 2s 定时刷新（POLL_MS=2000，源码实证；M6 已在 UI-FUNCTIONS.md 补登记） |
| 任务栏 (dclawos-shell) | GTK3 例外（Wnck 无 GTK4 等价物） | 完整 — 四槽位切换、48px + _NET_WM_STRUT_PARTIAL 几何逐字未动、M4 四态 agent 指示器；只对齐调色板 |
| 安装器 (dclawos-installer) | 非 GTK（Calamares，Qt/QML 包装脚本） | 代码就绪、安装成功未验证（见未验证清单）；live 与装后权限区分按 P3-M5 设计 |

### Streaming status (re-checked against the M3 code at M6)

| Format | Path | stream_mode |
|---|---|---|
| openai | SSE 真流式（`stream:true`，增量解析，delta 随到随发） | `"sse"` |
| passthrough | SSE 真流式（同一执行器） | `"sse"` |
| claude | 非流式 translator 分支，整段回复以 staged delta 显现 — 分阶段显现，非供应商流式 | `"staged"` |
| 其他注册表格式 | 与流式前一致地 fail closed（不支持的格式） | `"staged"`（在失败 envelope 上；不发出 delta） |

Post-delta failure semantics (M3, unchanged): a failure after
the first delta closes as an honest partial error
(`partial: true`) — no other provider silently continues a
half-written reply.

### Performance sanity （诚实口径）

- Structural fact, from the host code: a WebView (WebKit)
  process exists only while one of the three WebView windows
  （模型设置/聊天/看板） is open; closing the host window exits
  it. Every other surface （设置外壳/编辑器/监视/接管/审批/
  任务栏） is a native GTK process — there is no always-on
  browser engine in the system.
- No runtime performance measurement exists on target
  hardware: this phase was built and gate-tested headless.
  QEMU / real-machine visual checks and performance feel
  remain on the user's verification list (below); nothing in
  this ledger claims otherwise.

### 未验证与排队总清单 (unverified / queued — NOT done)

Unverified:
- GTK/WebView 视觉逐页未验：模型设置页、聊天页、看板页、
  设置外壳、编辑器、监视、接管、审批窗、任务栏指示器 —
  全部只过了静态门禁 + 无头逻辑门禁，页面内渲染/动画本身
  未在真显示上看过。
- Bookworm 精确世代运行时未实跑：M5 代码按 Bookworm API
  代际（GTK 4.8 / Adw 1.2 / GtkSource 5.6 / WebKitGTK
  6.0-2.50）写，本地冒烟用的是 Ubuntu 较新栈，CI 为无头
  静态/逻辑门禁（M5 台账已注明）。
- 真实供应商全链未验：环境内无真实密钥，一切供应商行为
  以本地 mock 验证（现行口径，见下方事件归档行）。
- Calamares 安装未验：安装器代码就绪，安装成功从未在
  QEMU/真机上跑通验证。
- partial 流式断线分支未单独断言：post-delta 失败的
  partial 语义在代码中实现，但没有一条门禁专门断言该
  分支（M3 门禁覆盖了 pre-delta 失败与正常流式）。

Queued （排队，未做，不许写成已完成）:
- P0 硬化清单仍排队：票据自批/防篡改、急停防篡改、
  shell 分级、web_fetch SSRF、systemd 加固、事件发布者
  认证（M1 台账已注明总线为新鲜度通道、非防篡改通道）。
- 范围授权 (scoped approval)：需先扩票据模型，排
  P0/易用性阶段（M4 台账 + UI-FUNCTIONS.md 排队行）。
- 接管窗/独立审批窗的列表轮询推送化（1s/2s POLL)：不在
  Phase 6 授权内，现状已如实登记。
- 后台 `process` 工具的实时输出：M1 登记的
  DEFERRED-NAMED，需 reader-thread + log-cursor 重构。
- WhatsApp 渠道：BLOCKED-NAMED，待用户拍板（公网 webhook
  vs Baileys 风险）。
- IRC / Mattermost 渠道：Phase 5 登记可行延后。
- Discord 网关 WebSocket、Slack 历史回补、Matrix E2EE、
  模型 lockout 跨进程可见性：均为各模块台账中的具名
  边界，未做。

### Incident archive — 2026-10-31 10:27 授权卡事件

One line, for the record (full account in the M3 Correction
record above, which this line cross-references and does not
repeat): at 10:27 CST a Muse permission card fired because
the first chattest no-key section let `llmlib.call_stream`
attempt a keyless explicit pin against a provider's DEFAULT
endpoint unauthenticated (no key, no user data, nothing was
sent authenticated) — root cause: no no-key short-circuit in
the library. Disposition: fixed in #54 (call_stream) and
extended in #55 / M3.1 (call() shares the same
`_short_circuit`); the standing rule since (user-calibrated
2026-10-31) is: provider verification uses local mocks, no
fabricated keys, and endpoint probes carry no user data.
