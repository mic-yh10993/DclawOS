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

## Module ledger

| Module | Commit | Run | Result |
|---|---|---|---|
| P6-M1 event bus + live output + task wake + bridge core | (this commit) | — | pending |

## Later modules (not started)

- M2 model settings WebView, M3 chat streaming WebView,
  M4 dashboard timeline WebView — all on the bridge core above.
- M5 GTK4 migration, M6 close-out.
