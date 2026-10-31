# DclawOS Host-Bridge Protocol (Phase 6 M1 foundation)

Version: `dclawos-bridge` protocol **1**. Core implementation:
`bridgeprotocol.py` (pure Python, stdlib only) — the validator /
allowlist core every WebView host imports. This document is the
contract for the later WebView modules (M2 settings, M3 chat,
M4 dashboard). M1 defines and tests the protocol core only; it
builds no WebView page.

## Trust model (hard rules)

- **Page content is untrusted.** A page is HTML/JS that may be
  extended by skills/themes later; treat every byte it sends as
  hostile input.
- The host **never evals** page-supplied code, never executes a
  page string as a command/path/template, and dispatches **only
  allowlisted verbs** to typed host handlers.
- **Secrets are write-only.** A secret value may cross the bridge
  only inside a command whose allowlist entry is marked
  secret-bearing (`secret_params: true`, today only
  `secret.set`). Replies and events never contain secret values:
  `bridgeprotocol.make_reply` / `make_event` strip secret-looking
  keys recursively, and logs must use `redact_params`.
- No hello, no commands: a page must negotiate first.

## Envelopes

All messages are JSON objects with `kind`, `protocol` (1) and,
for commands/replies, a request `id` (string, ≤128 chars) chosen
by the page; every reply echoes the id it answers.

Page → host command:

```json
{"kind": "command", "protocol": 1, "id": "r-42",
 "command": "chat.send", "params": {"text": "你好"}}
```

Host → page reply (success):

```json
{"kind": "reply", "protocol": 1, "id": "r-42", "ok": true,
 "result": { }}
```

Host → page error (also the reply to a refused command):

```json
{"kind": "error", "protocol": 1, "id": "r-42",
 "error": {"code": "not_allowlisted", "message": "..."}}
```

Host → page pushed event (e.g. a forwarded event-bus event):

```json
{"kind": "event", "protocol": 1, "type": "chat.message",
 "data": { }}
```

## Hello / version negotiation

Page sends `{"kind": "hello", "protocol": 1,
"protocol_name": "dclawos-bridge", "surface": "<surface>"}`.
Host answers `make_hello_ack(surface)`, which lists the verbs
allowlisted for that surface. An unsupported protocol version is
refused with `unsupported_protocol` — never silently downgraded.

## Per-surface allowlists (registry: `SURFACE_COMMANDS`)

| Surface | Verbs |
|---|---|
| host (all surfaces) | `hello`, `ping` |
| settings (M2) | `settings.get`, `settings.set`, `secret.set` (secret-bearing), `registry.list`, `model.catalog`, `model.provider.save`, `model.provider.delete`, `model.provider.override`, `model.provider.reset`, `model.config.get`, `model.config.save`, `model.health`, `model.test`, `model.usage` |
| chat (M3) | `chat.state`, `chat.send`, `chat.history`, `chat.assign`, `chat.switch`, `chat.new`, `chat.model.set`, `chat.approve`, `chat.deny`, `chat.stop`, `chat.copy`, `chat.launch` |
| dashboard (M4) | `dashboard.state`, `dashboard.approve`, `dashboard.deny`, `dashboard.launch` |

A verb allowlisted for one surface is refused on another.
Adding a verb requires a registry entry here, a typed host
handler, and a note in docs/PHASE6.md — in that order.

## Settings model verbs (Phase 6 M2)

All on the `settings` surface, implemented by typed handlers in
the `dclawos-settings` host (`_model_dispatch`). Only `secret.set`
is secret-bearing; every other verb refuses secret-looking params,
and all replies/events pass the secret stripping in
`bridgeprotocol.make_reply` / `make_event`.

| Verb | Params | Reply | Secret? |
|---|---|---|---|
| `model.catalog` | `{}` | `{providers: [...]}` — engine catalog merged with custom providers + builtin overrides; each entry carries `builtin`, `enabled`, `baseUrl`, `format`, `models`, `has_override`, `key_configured` (bool only), `catalog_models` | no |
| `model.provider.save` | `{provider: {id, name, baseUrl, format, models, enabled, ...}}` | `{provider: <normalised entry>}` — custom providers only; a builtin id is refused (use the override verbs) | no |
| `model.provider.delete` | `{provider: <id>}` | `{deleted: <id>}` — custom only; builtin ids are refused with the disable/reset explanation | no |
| `model.provider.override` | `{provider: <builtin id>, override: {enabled, baseUrl, addModels, hideModels}}` | `{provider, override}` — validated before persisting; bad input writes nothing | no |
| `model.provider.reset` | `{provider: <builtin id>}` | `{provider, override: null}` — deletes the overlay (= restore defaults) | no |
| `model.config.get` | `{}` | `{config: <model_config>, settings: <shared settings>}` | no |
| `model.config.save` | `{config: <full model_config>}` | `{config: <normalised config>}` — `registrylib.normalize_model_config` validates first; an invalid config is refused and NOT persisted | no |
| `model.health` | `{}` | engine status object (quality / breakers / rate_limits + per-section `*_status`, `lockouts`, `lockouts_available`, `lockouts_note`, `source`) | no |
| `model.test` | `{provider, model}` | `{ok, configured, provider, model, attempts, latency_ms, cost_usd, error}` — one minimal real request; `configured: false` means no key exists and NO request was sent | no |
| `model.usage` | `{}` | usage aggregation from `/var/lib/dclawos/usage.jsonl` (totals, per-provider / per-model rows, known vs unknown cost counts) | no |
| `secret.set` | `{name: "llm-<provider>", value: <key>}` | `{ok, message}` — write-only via the existing approval + broker write-secret flow; the value never appears in any reply or event | **yes (write-only)** |

## Chat verbs (Phase 6 M3)

All on the `chat` surface, implemented by typed handlers in
`chatlib.ChatHost.dispatch` (the `dclawos-chat` WebView host is a
thin GTK/WebKit shell around it). None is secret-bearing; the
approval params a page may see are redacted host-side
(`bridgeprotocol.redact_params`) before they cross, and replies
pass the bridge secret stripping as usual.

| Verb | Params | Reply | Secret? |
|---|---|---|---|
| `chat.state` | `{}` | `{sessions, current_session, messages, model: {provider, model, configured, providers}, approvals, help}` — sessions from convlib, messages = current transcript projection, model = merged catalog + shared-settings default + key-configured booleans only | no |
| `chat.history` | `{session}` | `{session, messages}` — transcript projection for one session | no |
| `chat.send` | `{text}` | `{action: "task", task_id, title}` or `{action: "reply", text}` — plain text creates an llm task bound to the current conversation; `/…` lines go through chatlib.parse_command (pre-M3 semantics) | no |
| `chat.assign` | `{title, goal, steps}` | `{action: "task", task_id, title}` — steps must be a JSON array | no |
| `chat.switch` | `{session}` | `{session, messages}` — validates the 16-hex session id, persists it as the current session | no |
| `chat.new` | `{}` | `{session, messages: []}` — creates + persists a fresh session | no |
| `chat.model.set` | `{provider, model}` | `{model}` — writes the global default via settingslib.apply_setting (ai-provider / ai-model), the M2 shared store | no |
| `chat.approve` / `chat.deny` | `{ticket_id}` | `{ticket_id, status}` — the host writes the verdict onto the ticket file exactly as dclawos-approve does (status / approved_by="human-gui" / approved_at via ticketlib); no other verdict channel exists | no |
| `chat.stop` | `{}` | `{stopped, message}` — the existing cooperative stop only (dclawos-agent-ctl stop) | no |
| `chat.copy` | `{text?}` | `{copied, length}` — host clipboard (Gdk 4 display clipboard in the shell since M5: `Display.get_clipboard().set(text)`; Gtk.Clipboard no longer exists in GTK4 — wording corrected at M6 to match the shipped host code); defaults to the last AI reply | no |
| `chat.launch` | `{target: dashboard\|monitor\|settings}` | `{launched}` — host-side Popen of the sibling app | no |

Pushed events to the chat page: event-bus events filtered
host-side to this window's tasks/conversation (llm deltas,
command/output step events, approval cards/verdicts), plus the
host-synthesised `chat.task_done` reconciliation event (exactly
once per finished known task, carrying the task result the page
reconciles its streamed bubble against).

## Dashboard verbs (Phase 6 M4)

All on the `dashboard` surface, implemented by typed handlers in
`dashboardlib.DashboardHost.dispatch` (the `dclawos-dashboard`
WebView host is a thin GTK/WebKit shell around it). These verbs
REPLACE the M1 registry placeholders (`dashboard.snapshot`,
`takeover.open`, `event.ack`), which no host ever implemented.
None is secret-bearing; approval params a page may see are
redacted host-side (`bridgeprotocol.redact_params`, via the
shared `chatlib.approval_card_view`) before they cross, and
replies pass the bridge secret stripping as usual.

| Verb | Params | Reply | Secret? |
|---|---|---|---|
| `dashboard.state` | `{}` | `{events, counts, usage, shell, takeovers, approvals, max_events}` — events = the one-time history snapshot from events.jsonl (file order, envelope ids kept for page-side dedupe), counts = task counts by status from agentlib (running / waiting=queued / done / failed / cancelled), usage = usagelib aggregation (known cost summed, null-cost calls counted separately as unknown — never 0), shell = the taskbar four-state view, takeovers / approvals = pending views | no |
| `dashboard.approve` / `dashboard.deny` | `{ticket_id}` | `{ticket_id, status}` — the host writes the verdict through `chatlib.apply_verdict`, the ONE shared write (status / approved_by="human-gui" / approved_at via ticketlib) used by dclawos-approve and the chat host; no other verdict channel exists, and there is NO task-scoped grant verb: ticketlib is a per-operation single-ticket model (see docs/PHASE6.md M4, scoped approval is a queued item) | no |
| `dashboard.launch` | `{target: takeover\|monitor\|chat\|dashboard\|settings\|editor\|approve}` | `{launched}` — host-side Popen of the sibling app (the takeover banner's click path is `target: "takeover"`) | no |

Pushed events to the dashboard page: every display event-bus
event (deduped host-side against the history snapshot by
envelope id), forwarded as a bridge `event` whose `type` is the
bus event's type and whose `data` is the full bus envelope
(secret-stripped); plus the host-synthesised `approval.card`
items (redacted card payloads / verdict notices) and
`dashboard.counts` items (counts + usage + shell state +
pending takeovers/approvals, recomputed by the host when a
relevant bus event arrives — the page never polls).

## Event forwarding shape

Hosts forward event-bus events (docs/PHASE6.md, eventbus
protocol v1) to pages as bridge `event` messages: `type` is the
bus event's type, `data` carries the bus event envelope minus any
secret-looking keys (stripped by `make_event`). Bus-only signals
(`data.display == false`) are host-internal and are not forwarded
as display events.

## Validation

`bridgeprotocol.validate_hello` and `validate_command` raise
`BridgeError` (with the wire error shape via `to_error()`) for:
non-object messages, wrong kind, unsupported protocol, missing /
oversized id, unknown surface, non-allowlisted verb, non-object
params, and secret params on a non-secret-bearing command.
