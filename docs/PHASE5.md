# DclawOS Phase 5 — Channels, Self-Provisioning, UI Craft

User directive (2026-10-31, verbatim): 「要第三方消息渠道的支持」
「要确保agent能够调用一切，比如说agent可以自己安装skills啊，
mcp啊，不需要手动操作」「这些都要慢慢配置，不急慢慢来」
「还有各个页面协调点，高质量丝滑一些，按大厂质感写前端，
严守10条铁律」(the 10 rules are recorded verbatim in
docs/UI-RULES.md).

This directive SUPERSEDES the Phase 4 gap ledger §D exclusion of
third-party messaging channel plugins (docs/PHASE4-GAP.md): that
exclusion is revoked by the user. Pace is user-set: quality and
completeness over speed; many small verified modules.

Status vocabulary: OPEN · WIP · CLOSED (implemented, build-
verified against local mocks where external services are
involved) · BLOCKED-NAMED (cannot be implemented solidly;
blocker + evidence recorded here, never faked).

Method: reuse-first retrieval. OpenClaw channel architecture
inventory: `research/dclawos-reboot/PHASE5-CHANNEL-INVENTORY.md`;
WS2 capability audit: `research/dclawos-reboot/PHASE5-WS2-AUDIT.md`
(both kept in the research tree; the closing state of every row
below is authoritative in THIS file).

## WS1 — Third-party messaging channels

Design: channels ride the Phase 4 C4 runtime, NOT a parallel
stack. A channel is a registry entry (registrylib category
`channels`: {id, kind, name, enabled, allowlist[], secret,
options{}}) managed in Settings and by agent tools. `channellib`
polls enabled channels inside the agent daemon's existing
inbound cycle; each allowlisted inbound message becomes an `llm`
task on a per-(channel, chat) conversation; the pending-reply
machinery (same pending file as connectors) sends the final
reply back through the same channel to the originating chat.
Non-allowlisted senders create NOTHING (consumed silently by the
adapter cursor; logged as a count in the channel state file).
Failure / no-model ⇒ no reply is sent (no fabricated text).
Tokens: entered by the USER in Settings → 渠道， stored via the
broker write-secret op as secret `channel-<id>` (0600 files,
write-only — same pattern as provider keys). Tests use local
mock channel servers only; no real token exists in CI.

Reference (OpenClaw clone): channel core `src/channels/`
(registry.ts; allowlists `allow-from.ts`; plugins catalog
`plugins/catalog.ts` L326+/L411 per PHASE4-OPENCLAW-INVENTORY
§5); concrete channels live in `extensions/{telegram,discord,
slack,whatsapp,signal,matrix,…}`. DclawOS transfers the
SEMANTICS (normalised inbound event, allowlist-first policy,
reply-to-origin) into the stdlib-Python connector-shaped
runtime; upstream adapters are TypeScript + per-service SDKs
and are not copied (see per-file // reuse-check notes).

| # | Item | State |
|---|---|---|
| W1.0 | Channel framework: registry category, channellib, daemon wiring, pending-reply via channel+chat, settings 渠道 page | OPEN (P5-M1) |
| W1.1 | Telegram (Bot API long-poll getUpdates; sendMessage) | OPEN (P5-M1) |
| W1.2 | Discord (REST poll of a configured channel; send) | OPEN (P5-M2) |
| W1.3 | Slack (Socket Mode; needs app+bot tokens and a websocket client path) | OPEN (P5-M2, evaluation-gated) |
| W1.4 | WhatsApp (evaluation: Cloud API is webhook-only inbound; Baileys is unofficial/QR) | OPEN (P5-M3, evaluation) |
| W1.5 | Matrix (client-server /sync long-poll, user access token) | OPEN (P5-M3) |
| W1.6 | Signal / Teams / Feishu / LINE / others in reference | OPEN (evaluated in P5-M3; named outcome per channel) |

## WS2 — Agent can invoke everything (self-provisioning)

Design: audit every human-facing capability against the agent
tool set (research audit file above); fill every gap with a
tool wired through agentlib.classify + toolslib schemas +
daemon dispatch — the SAME three places every tool lives.
Install-class actions are tier 2 (code-execution class) unless
agent-full-access is on; every install writes a dashboard event
with source URL/path + license; validation failure rolls the
registry entry back (no half-installed state). Secrets remain
write-only for the agent: the agent can never read a secret
value, and channel/provider TOKENS stay human-entered (an agent
tool may enable/disable a channel or edit its allowlist, never
set its token — recorded as a deliberate boundary in the audit).

| # | Item | State |
|---|---|---|
| W2.0 | Capability audit table (human path vs agent tool) | OPEN (research; lands P5-M4) |
| W2.1 | skill_install / skill_remove (local path or git URL; license+provenance recorded; validate then register) | OPEN (P5-M4) |
| W2.2 | mcp_add / mcp_remove (stdio command or URL; MUST pass a live MCP initialize+tools/list handshake before the entry is kept) | OPEN (P5-M4) |
| W2.3 | channel_manage (enable/disable, allowlist edit; no token writes) | OPEN (P5-M4) |
| W2.4 | Remaining audit gaps (per audit table; each row named) | OPEN (P5-M4) |
| W2.5 | Settings GUI reflects agent-side changes (single registry file, two writers — already true by construction; verified in gate) | OPEN (P5-M4) |

## WS3 — Frontend quality (the 10 iron rules)

Full mapping: docs/UI-RULES.md (rule → user wording → GTK
implementation → deviation). Implementation lands in theme.py /
theme.css (+ theme-dark.css, ui-dark schema key + Settings
toggle), then per-app application points (chat reveal + copy
feedback, settings revealer sections, numeric classes, taskbar
palette alignment).

| # | Item | State |
|---|---|---|
| W3.0 | docs/UI-RULES.md mapping (all 10 rules) | CLOSED (P5-M0, this doc set) |
| W3.1 | Palette/focus/tnum tokens in theme.css + dark variant + ui-dark toggle | OPEN (P5-M5) |
| W3.2 | Chat stream-reveal (rule 4) + copy-feedback 2.5 s (rule 10) | OPEN (P5-M5) |
| W3.3 | Revealer sections (rule 7), numeric classes (rule 6), prose caps (rule 8), feedback helper (rules 9/10) across apps | OPEN (P5-M5) |
| W3.4 | Taskbar palette/typography alignment (layout untouched) | OPEN (P5-M5) |

## Module ledger

| Module | Commit | Run | Result |
|---|---|---|---|
| P5-M0 ledger + UI-RULES docs | (this commit) | — | pending |

## Unverified / honesty boundary

- All channel behaviour is verified against LOCAL MOCK servers
  in the build gate. Real-service verification needs the user's
  own bot tokens on a real machine; nothing here claims it.
- GTK visual quality cannot be screenshot-verified in the
  headless build VM; WS3 is verified by gate token assertions +
  code-level application, and needs an on-device visual pass.
