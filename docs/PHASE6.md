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

## Module ledger

| Module | Commit | Run | Result |
|---|---|---|---|
| P6-M1 event bus + live output + task wake + bridge core | (this commit) | — | pending |
| P6-M2 model settings complete (WebView + routing wiring + usage ledger) | (this commit) | — | pending |
| P6-M3 chat WebView + true streaming (SSE openai/passthrough, staged claude) | (this commit) | — | pending |

## Later modules (not started)

- M2 model settings WebView, M3 chat streaming WebView,
  M4 dashboard timeline WebView — all on the bridge core above.
- M5 GTK4 migration, M6 close-out.
