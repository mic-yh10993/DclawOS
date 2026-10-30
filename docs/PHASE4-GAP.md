# DclawOS Phase 4 — Full-Transplant Gap Inventory

User order (2026-10-30, verbatim): 「你继续写啊，完整一移植，要完全能够
替代openclaw」 — full transplant; the finished system must be able to
completely replace OpenClaw. This supersedes every Phase 3 "in-scope /
subset" limit: all Phase 3 honest gaps are Phase 4 work items.

Method: reuse-first retrieval against the two reference clones
(OpenClaw `c6999b9f`, OmniRoute 3.8.53 `5332fe88`). Full inventories:
`research/dclawos-reboot/PHASE4-OPENCLAW-INVENTORY.md` and
`PHASE4-OMNI-INVENTORY.md` (kept in the research tree; the closing
state of every row below is authoritative in THIS file).

Status vocabulary: OPEN (not started) · WIP · CLOSED (transplanted,
functionally equivalent, build-verified) · EXCLUDED (non-core, with
reason — user-visible, never silent).

## A. LLM call path & agent loop (critical path)

| # | Capability | Reference (path:line) | DclawOS at Phase 3 end | Phase 4 plan | State |
|---|---|---|---|---|---|
| A1 | Provider call path (OpenAI-compatible) | OmniRoute `open-sse/executors/default.ts` (`DefaultExecutor.execute`: URL normalise → auth header → fetch → usage extraction); OpenClaw `packages/ai/src/providers/openai-completions.ts` | Absent: `dclawos-route` decides, nothing calls | New engine entry `dclawos/llm.ts`: registry baseUrl resolution + fetch executor behind the dispatch pipeline; key arrives via stdin from the agent service (0600 secret files), never argv/log/repo | WIP (P4-M1) |
| A2 | Anthropic Messages path | OmniRoute translator pair `open-sse/translator/request/openai-to-claude.ts` + `response/claude-to-openai.ts`; executors `claude.ts`, `anthropicOAuth.ts` | Absent | Copy the translator pair (MIT) into engine-src; executor branch for `anthropic` (x-api-key, anthropic-version) using the copied translators | WIP (P4-M1: adapted conversion in llmClaude.ts, locally mock-verified; verbatim translator copy remains the completion step) |
| A3 | Dispatch pipeline is real (not skeleton) | OmniRoute `open-sse/services/combo.ts` (target expansion/prelude/attempt loop), `handlers/chatCore.ts` (3,416 lines; non-streaming slice ~8 files) | `dclawos/dispatch.ts` skeleton: stages 1–5 real scoring/order/retry, stage 6 is a caller callback | Wire stage 6 to the real executors (A1/A2); account-fallback classification (B1) feeds durable/transient decisions; nested-combo expansion (`resolveComboTargets`) ported for combo targets | WIP (P4-M1: stage 6 wired to real executors; B1/B5 hooks land in P4-M3) |
| A4 | Agent loop (LLM → tool call → approval → execute → feed back → continue) | OpenClaw `packages/agent-core/src/agent-loop.ts:92,248,497` (`runAgentLoop`, tool-batch execution); approval seam `src/agents/agent-tools.before-tool-call.approval.ts:495,517` | Absent: daemon executes pre-written `steps[]`; results never fed back | Loop in `dclawos-agent` for chat/llm tasks: model tool_calls → existing `execute_step` (tier classify + ticket approval, unchanged) → tool-result messages appended → re-call; stop on final answer / max turns / stop flag / budget | OPEN |
| A5 | Model-facing tool schemas | OpenClaw `src/agents/agent-tools.ts:184,254,677` (runtime tool construction), `core-coding-tools.ts:204` | Absent: 13 fixed dispatch tools, no schemas | `toolslib` schema table (name/description/JSON-Schema) for every dispatch tool, serialised into the request `tools[]`; schemas and dispatch table kept in one place | OPEN |
| A6 | Session / conversation persistence | OpenClaw `src/config/sessions/paths.ts:65,306` (`sessions.json` + `<id>.jsonl` transcripts); `packages/agent-core/src/harness/session/session.ts` | Partial: task JSON queue; `session` is an opaque tag; chat keeps no history | Per-session transcript store `/var/lib/dclawos/conversations/` (`sessions.json` index + `<session>.jsonl`), written by the loop, loaded/rendered by `dclawos-chat`, resumable across tasks | OPEN |
| A7 | Chat window produces real replies | OpenClaw chat surface over the loop (above) | Honest no-model placeholder | Plain chat messages create loop tasks bound to the window's session; with a provider configured in Settings (+ secret), replies are real model output; unconfigured stays honestly model-less | OPEN |

## B. OmniRoute transplant completion

| # | Capability | Reference (path:line) | DclawOS at Phase 3 end | Phase 4 plan | State |
|---|---|---|---|---|---|
| B1 | accountFallback FULL (classification, model lockout, provider cooldown/breaker, 429 semantics) | OmniRoute `open-sse/services/accountFallback.ts` (2,569 lines; exports :750 lockModel, :812 recordModelLockoutFailure, :1038 isModelLocked, :1228 recordProviderFailure, :1290 recordProviderSuccess) + ~39-file closure (~9,700 lines total) | Only `lockoutStore.ts` read semantics (threshold/cooldown Map); classification lived in dispatch's own table | Copy the closure verbatim (MIT) into engine-src; the single DB touchpoint (`@/lib/db/providers` `setConnectionRateLimitUntil`, accountFallback.ts:68) goes to ONE new adapter file backed by the established file-state store — same field, same semantics, persisted; dispatch/llm wire `recordProviderFailure/Success` + lockout checks. NOT a read-only mirror this time | OPEN |
| B2 | routing/quality.ts + storage backend | OmniRoute `open-sse/services/routing/quality.ts` (327 lines; :124 recordQualityEvent, :235 getProviderQuality, :281 getQualityScore — in-memory EWMA, no persistence upstream) | `quality` factor exists in scoring (weight 0.03) but is caller-supplied only | Copy quality.ts verbatim; ADD the persistence upstream lacks: file-state backend (same store pattern) so EWMA state survives restarts; executor records events per real call; decision path loads persisted quality into telemetry | OPEN |
| B3 | chaosEngine | OmniRoute `open-sse/services/autoCombo/chaosEngine.ts` (674 lines; :215 runChaosPanel, :434 handleChaosChat, :631 dispatchChaosFromCombo) + its test file | Absent (named as gap in Phase 3) | Copy chaosEngine.ts + its closure + upstream test adapted as a bundle selftest (deterministic parts); wire `dispatchChaosFromCombo` behind an explicit settings/env opt-in (default OFF — chaos injects faults, never on by default) | OPEN |
| B4 | cost_envelope traced + implemented | Emission traced: gateway computes per-request cost via `src/lib/usage/costCalculator.ts:325,361` (`calculateCostDetailed`/`calculateCost`) called from `handlers/chatCore/streamingCost.ts` / non-streaming usage stats; the response `cost` is then surfaced as `cost_envelope {estimated, actual, currency:"USD"}` by `src/lib/a2a/skills/smartRouting.ts:36,82` | `costUsd` null; no envelope anywhere | Executor computes cost from REAL response usage × registry pricing (`providerCostData.getModelPricing`, the vendored local pricing source — documented equivalent of calculateCost's DB pricing lookup) and emits `cost_envelope` on every executed request; no response ⇒ no envelope, cost stays null | WIP (P4-M1) |
| B5 | combo target expansion | OmniRoute `open-sse/services/combo.ts` (`resolveComboTargets`, `resolveNestedComboTargets`, cycle guard) | dispatch stage 1 passes the ordered list through | Port expansion + cycle guard into the dispatch path used by llm.ts (JSON combo definitions from the settings registry, as with taskFitness' JSON adaptation) | OPEN |

## C. OpenClaw core completeness

| # | Capability | Reference (path:line) | DclawOS at Phase 3 end | Phase 4 plan | State |
|---|---|---|---|---|---|
| C1 | Subagent runtime (own session/model, result delivery) | OpenClaw `src/agents/tools/sessions-spawn-tool.ts` (711 lines), `sessions-send-tool.ts`, `subagents-tool.ts` (457) | Partial: sequential child tasks, blocking poll, no own session | Child = own conversation (A6) + own loop run (A4) + optional model pin; parent receives final assistant text; daemon runs tasks concurrently (bounded pool) so a waiting parent no longer blocks the queue; depth limit enforced | OPEN |
| C2 | Scheduling / cron | OpenClaw `src/cron/service.ts` (façade), `store.ts:485`, agent `cron` tool family | Absent | Job store (registrylib patterns) + daemon timer firing loop/step tasks + `cron` tool in the loop + CLI management; delivery = event log + optional connector_send | OPEN |
| C3 | Memory + context assembly + compaction | OpenClaw `packages/agent-core/src/harness/compaction/compaction.ts` (730), `src/context-engine/types.ts:37` | Absent | Context assembly v1 (system prompt + transcript window); compaction: when a transcript exceeds the turn/token budget, older turns are summarised by the loop's own model call into a `compaction` transcript entry (OpenClaw entry shape); file memory store + `memory_save`/`memory_search` tools (plain-text search; no vector backend — see exclusions) | OPEN |
| C4 | Connector inbound runtime | OpenClaw `src/channels/` contract (inbound normalisation → agent run → outbound send), 45k LOC core | Partial: outbound one-shot send, one local adapter | Inbound: daemon polls enabled connectors that implement `poll` (same stdin/stdout JSON contract, extended); inbound events create loop tasks on a per-connector session and replies go back via `send`; local adapter implements poll over its inbox file | OPEN |
| C5 | Extra core tools (edit, process, web_fetch) | OpenClaw `core-coding-tools.ts:204` (`edit`/`apply_patch`, background `process`), `tools/web-fetch.ts` | Absent (full-file write + one-shot shell only) | Add `edit_file` (exact-match replace), `process` (background shell with poll/log/kill over the task store), `web_fetch` (HTTP GET → text, size-capped) with classify() tiers | OPEN |

## D. Explicit exclusions (non-core; user-visible, with reasons)

From the OpenClaw inventory §9 — absence of any of these does NOT
prevent replacing OpenClaw as this single-user desktop OS agent:

- Third-party messaging channel plugins (Telegram/WhatsApp/Discord/
  Slack/Signal/Matrix/Teams/Feishu/…): per-service adapters for
  accounts the OS does not own; the human is at the machine (GTK
  chat/approval/takeover exist); the local connector contract (C4)
  leaves a per-service door open without porting the gateway.
- Gateway as a network server (remote clients, node pairing,
  WebSocket protocol, cloud placement): DclawOS's trust boundary is
  local (Unix-socket broker + tickets); a network gateway would
  reintroduce the auth/pairing attack surface the ticket model
  replaced.
- Companion apps (Android/iOS/macOS nodes): out of OS scope; the
  node is the machine itself.
- Media generation/understanding suites, TTS/STT, meeting bots:
  provider-billing features, addable later as MCP servers/skills.
- Hosted browser/computer-use SaaS: DclawOS already has local GUI
  operation + human takeover (Phase 2/3).
- Vector/semantic memory backends (lancedb etc.) and "dreaming":
  file memory + compaction (C3) first; embeddings infra is optional
  weight on a 2-core image.
- ~60 provider-brand extensions & local-inference runtimes: the
  API-family seam (A1/A2) covers any OpenAI/Anthropic-compatible
  endpoint; brands are config, not code; local inference does not
  fit the target hardware.
- ACP/external-harness bridges, plugin marketplace, workboard/
  team/a2a/session-share, enterprise observability plugins,
  Postgres state engine, multi-agent roster: multi-tenant/hosted
  collaboration features; single-user local files suffice.
- OmniRoute web/OAuth executor zoo (claude-web, chatgpt-web,
  antigravity OAuth families, codex app-server, …): these drive
  third-party web sessions with cookie/OAuth custody — incompatible
  with the local secret-file model and the "no credentials in repo,
  keys only via Settings GUI" rule. The API-key executor families
  (OpenAI-compatible + Anthropic) are the transplant target (A1/A2).

## E. Module plan (each: independent commit → push main → API-verified run)

1. **P4-M1** Provider call path + real dispatch + cost_envelope (A1, A3, B4; Anthropic A2 if closure allows, else M1b).
2. **P4-M2** Agent loop + tool schemas + transcripts + chat wiring (A4, A5, A6, A7) with mock-endpoint end-to-end gate.
3. **P4-M3** accountFallback full transplant + DB adapter (B1, B5).
4. **P4-M4** quality.ts + persistence + wiring; chaosEngine transplant (B2, B3).
5. **P4-M5** Subagent runtime upgrade + extra core tools (C1, C5).
6. **P4-M6** Cron scheduling (C2).
7. **P4-M7** Memory + compaction (C3).
8. **P4-M8** Connector inbound runtime (C4) + this doc's closing audit.

Verification rule for LLM paths: no real API keys exist in CI and
none may be invented or committed. End-to-end verification uses a
local mock OpenAI-compatible endpoint inside the build/gate
environment (loop, tool-call round trip, routing decision, cost from
mock usage fields). Real-provider verification happens on-device
after the user enters a key in the Settings GUI.
