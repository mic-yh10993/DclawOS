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
| W1.0 | Channel framework: registry category, channellib, daemon wiring, pending-reply via channel+chat, settings 渠道 page | CLOSED (P5-M1): registrylib `channels` category + channellib (adapter lazy-load by kind, central allowlist filter — a sender not on the allowlist is dropped and counted in channel state, adapters cannot widen it; per-channel state file = adapter cursor). Daemon polls channels on the existing connector cycle; replies flush via the shared pending map ({"channel", "chat"} routes). Settings 渠道 page ships in P5-M5's settings landing (single settings binary). Gate: hook 1008 channeltest (mock servers) + validate assertions |
| W1.1 | Telegram (Bot API long-poll getUpdates; sendMessage) | CLOSED (P5-M1): stdlib Bot API adapter; adapter-owned offset cursor advances past EVERY update (exactly-once); text messages only; sendMessage chunked at 4000; chat fallback state.last_chat → options.default_chat. Mock-gated |
| W1.2 | Discord (REST poll of a configured channel; send) | CLOSED (P5-M2): REST short-poll of ONE configured channel (options.channel_id) on the daemon cycle; snowflake cursor; bot's own messages skipped (id learned via /users/@me); send chunked at 2000. NAMED TRADEOFF: the gateway WebSocket transport was NOT implemented — the adapter contract is poll-shaped and the 30 s cycle bounds latency; gateway session supervision would be a separate larger module |
| W1.3 | Slack (Socket Mode; needs app+bot tokens and a websocket client path) | CLOSED (P5-M2): Socket Mode via a per-channel background receiver thread (apps.connections.open → websocket-client, envelope ack, disconnect → reconnect; only message events, subtypes/bot messages skipped); poll() drains the buffer. Two secrets: bot token (entry secret) + app token (secret name + "-app"); needs python3-websocket (packaged). NAMED BOUNDARY: Socket Mode has NO history backfill — messages sent while the machine was off are never recovered |
| W1.4 | WhatsApp (evaluation: Cloud API is webhook-only inbound; Baileys is unofficial/QR) | BLOCKED-NAMED (P5-M3): (a) official Cloud API delivers inbound ONLY as webhooks to a public HTTPS endpoint — a headless home system has none and DclawOS runs no public ingress by design; outbound-only would fake support. (b) The Baileys route upstream ships (extensions/whatsapp, login via QR pairing state machine) binds a PERSONAL account through unofficial protocol emulation: brittle against protocol changes and a ToS/account-ban risk for the user's own number. Not faked. Revisit only if the user accepts a public endpoint or the Baileys risk explicitly |
| W1.5 | Matrix (client-server /sync long-poll, user access token) | CLOSED (P5-M3): /sync with since-token (options.homeserver required); FIRST poll only establishes next_batch (no history replay); own messages skipped (whoami). NAMED BOUNDARY: no E2EE — m.room.encrypted events advance the cursor but never become tasks (upstream crypto SDK is out of stdlib scope); use unencrypted rooms. Mock-gated |
| W1.6 | Signal / Teams / Feishu / LINE / others in reference | NAMED OUTCOMES (P5-M3, evidence: PHASE5-CHANNEL-INVENTORY §8): Signal BLOCKED-NAMED — needs an external signal-cli daemon (extensions/signal accounts.ts:232); linking is a daemon operation, not a token; own packaging module if wanted. LINE BLOCKED-NAMED — webhook-only official API + public HTTPS. Feishu/Lark BLOCKED-NAMED for now — webhook mode needs a public endpoint; the WS long-connection mode exists only via the official JS SDK (a stdlib implementation is its own project). Google Chat BLOCKED-NAMED this phase — service-account + Workspace-admin provisioning dominates, not a Settings-completable bot-token flow. iMessage BLOCKED-NAMED (platform) — macOS-only (~/Library/Messages/chat.db via imsg bridge), DclawOS is Linux. IRC EVALUATED-FEASIBLE, deferred — plain TCP/TLS, token-free; a small stdlib adapter can land later without framework changes. Mattermost EVALUATED-FEASIBLE, deferred — token REST+WS against a self-hosted server |

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
| W2.0 | Capability audit table (human path vs agent tool) | CLOSED (P5-M4): full table = research PHASE5-WS2-AUDIT (G-1…G-22). Dispositions: six tools built (W2.1–W2.3 + registry_list); secret_set NOT built — deliberate boundary (tokens stay human-entered, supersedes audit G-8's suggestion; the pairing instruction outranks the audit); settings_set BLOCKED-NAMED below (W2.4); human-only BY DESIGN: full-access toggle (G-13), approval verdicts (G-14), takeover verdicts (G-15), clearing the agent's own stop flag (G-16), installer launch (G-19) — the agent may REQUEST these (takeover_request) but never grant them to itself |
| W2.1 | skill_install / skill_remove (local path or git URL; license+provenance recorded; validate then register) | CLOSED (P5-M4): sources = local dir, local .tar.gz/.zip, http(s) archive URL, git URL (git packaged in base set). Package must contain SKILL.md + a runnable command (param, or scripts/run.sh / skill.py / index.js); single wrapping dir flattened. License REQUIRED (refused without); staged validation → land in skills-store → register once; any failure leaves no entry and no payload. skill_remove deletes the payload ONLY for agent-installed store entries. Tier 2. Installed skills are immediately runnable via skill_run (gate-verified end to end) |
| W2.2 | mcp_add / mcp_remove (stdio command or URL; MUST pass a live MCP initialize+tools/list handshake before the entry is kept) | CLOSED (P5-M4): mcpclient gained a Streamable HTTP transport (Mcp-Session-Id, JSON or SSE-framed replies; env = headers for HTTP entries) + validate_server(). mcp_add registers NOTHING until initialize + tools/list succeed against the real server; the returned tool list is reported. Env guard: literal values for secret-looking keys are refused (secret:<name> references only). Tier 2. Gate: provisiontest handshakes a tiny stdio server AND a mock HTTP server, and calls tools on both |
| W2.3 | channel_manage (enable/disable, allowlist edit; no token writes) | CLOSED (P5-M4): actions list (tier 0) / add / remove / enable / disable / set_allowlist (tier 2). add records the conventional secret name channel-<id> + accepts adapter options (channel_id, homeserver…); the result reports token_configured — the token itself can never be set or read through the tool |
| W2.4 | Remaining audit gaps (per audit table; each row named) | CLOSED with named dispositions (P5-M4): registry_list built (tier 0, env redacted — literals shown as <set>, only secret: refs readable). settings_set: was BLOCKED-NAMED (GSettings is per-user dconf — Settings writes as the human user, the daemon reads as dclaw-agent; a daemon-side `gsettings set` would land in a database the GUI never reads = fake implementation). **CLOSED in P5-M7 via option B** — see the P5-M7 section below; user decision recorded there. G-18 (a general gui tool) EVALUATED, not built: dclawos-gui exists but wiring arbitrary GUI actuation as a tool would bypass the takeover path for credential-class operations; GUI work goes through takeover_request by design. G-17 no gap (subagent + cron). G-20 covered by write_file tier 2. TWO AUDIT-FOUND BUGS FIXED in this module: (1) broker param-name mismatch — model schemas say {name}, the broker wants {unit}/{package}; daemon dispatch now translates (model-issued service/package calls previously failed); (2) shell delete commands (rm/rmdir/unlink/shred argv) classified tier 1 although the privilege model says deleting is tier 2 — now tier 2 (obvious argv shapes; not a shell parser). Plus the stale dclawos-privileged header corrected (it claimed package-install was refused; 5 ops since Phase 3) |
| W2.5 | Settings GUI reflects agent-side changes (single registry file, two writers — already true by construction; verified in gate) | CLOSED (P5-M4): one registry file; every runtime parser re-reads it per call (no daemon restart needed); Settings re-reads on panel refresh. provisiontest exercises agent-side writes through the same registrylib the GUI uses |

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
| W3.1 | Palette/focus/tnum tokens in theme.css + dark variant + ui-dark toggle | CLOSED (P5-M5): theme.css rewritten on the binding palette (light #f5f5f3/#fcfcfb layers, double-ring focus, no borders, no #000); theme-dark.css ships the dark layers (#0b0b0a/#151515/#1a1a19/#20201e, weight-360 request per rule 3); `ui-dark` GSettings key + Settings toggle; theme.py FALLBACK strings byte-identical to the shipped CSS (verified). Gate-asserted (palette literals, #000 absence with comments stripped, ui-dark in schema + settings) |
| W3.2 | Chat stream-reveal (rule 4) + copy-feedback 2.5 s (rule 10) | CLOSED (P5-M5): theme.stream_reveal (30 ms tick, 3-step alpha ramp per batch, 90 ms/30 ms stagger character as documented deviation — GTK TextView has no per-glyph blur); chat replies render through it; “复制回答” copy button with flash_feedback (✓ + 2.5 s auto-restore, rule 10). Gate-asserted tokens |
| W3.3 | Revealer sections (rule 7), numeric classes (rule 6), prose caps (rule 8), feedback helper (rules 9/10) across apps | CLOSED (P5-M5): theme.make_section (GtkRevealer) applied to Settings registry groups + dashboard takeover banner; apply_tnum on chat/monitor/shell numeric labels (+ CSS font-feature-settings "tnum"); apply_prose on settings/monitor prose; flash_feedback also on settings secret-save status and editor save status. Per-app detail + deviations in docs/UI-RULES.md |
| W3.4 | Taskbar palette/typography alignment (layout untouched) | CLOSED (P5-M5): dclawos-shell calls apply_theme inside a guard (theme failure can never kill the shell); palette scoped via window.dclawos-taskbar in theme.css/theme-dark.css; TASKBAR_HEIGHT=48 and all geometry unchanged. POLICY NOTE: this reverses Phase 3 M7's deliberate taskbar exclusion per the user's Phase 5 directive; the validate gate's old negative assertion was replaced by the positive form with the reason recorded in the script + UI-RULES.md |

## Module ledger

| Module | Commit | Run | Result |
|---|---|---|---|
| P5-M0 ledger + UI-RULES docs | 25bffbf | 41090974570 (#43) | success |
| P5-M1..M3 WS1 channels (framework, Telegram, Discord, Slack, Matrix) | e799669 | 41093929086 (#44) | success |
| P5-M4 WS2 self-provisioning (provisioninglib, HTTP MCP, audit fixes) | 4124af3 + 1cf752d (fix) | 41097319110 (#46) | success — #45 (41096427145) failed at the gate: provisiontest hit Python 3.12-only tarfile filter= on bookworm's 3.11; product fixed with a portable hand-rolled safe extraction (1cf752d), hostile-archive case re-verified |
| P5-M5 WS3 UI craft (theme system, dark variant, per-app application) | 54504a0 | 41099855681 (#47) | success (release dclawos-2.0-preview-47, ISO 1,060,050,944 B) |
| P5-M6 close-out (ledger finalisation, this note) | (this commit) | — | pending |
| P5-M7 settings_set via shared config file (option B) | (this commit) | — | pending |

## Unverified / honesty boundary

- All channel behaviour is verified against LOCAL MOCK servers
  in the build gate. Real-service verification needs the user's
  own bot tokens on a real machine; nothing here claims it.
- GTK visual quality cannot be screenshot-verified in the
  headless build VM; WS3 is verified by gate token assertions +
  code-level application, and needs an on-device visual pass.

## Phase 5 close-out (P5-M6)

All three workstreams are landed and build-verified: WS1 four
adapters CLOSED with three channels BLOCKED-NAMED and two
EVALUATED-FEASIBLE-deferred (W1.6); WS2 six tools CLOSED with
settings_set at the time BLOCKED-NAMED pending a config-channel
decision (closed later by P5-M7, option B — see below) and the
human-only set recorded at
W2.0; WS3 all ten rules LANDED or CODE with per-rule deviations
in docs/UI-RULES.md. Unchanged unverified layers from earlier
phases: Calamares install success, real-provider LLM replies
(need the user's keys in Settings), on-device GTK visual pass.
The M4 row carries the phase's one red build (#45) — the gate
caught a real Python-version portability bug in product code;
the fix is the module's second commit.

## P5-M7 — settings_set via shared config file (follow-up module)

**Decision**: the user chose option B on 2026-10-31 (chat),
verbatim: "B：设置改存双方共读的配置文件" (settings move to a
config file both sides read). The broker set-setting option (A)
was NOT taken. This closes the W2.4 settings_set BLOCKED-NAMED row.

**What landed** (`settingslib.py`, single implementation, both
sides): the store is `/var/lib/dclawos/agent-settings.json`
(group `dclawos-session`, 0660, flock + per-key read-modify-write
+ atomic rename — the registry/tasks sharing pattern). The path
and the `chaos-enabled` key predate the module: the engine's
qualityStore chaos gate already read exactly this file, so the
chaos consumer migrated with zero engine changes. Key inventory
(= KEY_SPECS): the seven org.dclawos.settings schema keys +
chaos-enabled, all type/enum-validated; unknown keys rejected on
write, wrong-typed stored values fall back per-key, a corrupt
file falls back to all defaults (logged; next write sets it
aside as `.corrupt`) — no reader ever crashes.

**Migration**: on first run the settings GUI seeds the file from
the human's current GSettings values (defaults where untouched);
after that the file is authoritative and the GSettings schema
remains only as seed source / key inventory documentation (hook
1002 still compiles it). All former readers switched to the lib:
GUI (via a Gio-shaped adapter + focus-in refresh, so agent-made
changes appear without restart), agentlib.full_access_enabled,
llmlib.configured (provider/model/routing), theme ui-dark,
dclawos-shell window-mode. This also fixes the latent Phase 3/4
bug the audit implied: the daemon's gsettings reads saw
dclaw-agent's own dconf (always defaults), so the GUI's
full-access toggle and AI choices never reached the daemon.

**Tools**: settings_get (tier 0) / settings_set (tier 2) wired
through toolslib + agentlib.TOOLS/classify + daemon dispatch.
**Denylist (hard boundary)**: `agent-full-access` and
`chaos-enabled` are human-only — refused in execute_step BEFORE
any ticket exists (so full-access auto-approval cannot reach
them), re-checked in the lib (agent=True), attempts logged to
the event stream; successful sets log key + old→new. (Approval
tiers are not settings — they are hardcoded in classify, so no
tier-policy key exists to deny. Secrets are not settings either:
tokens stay in the broker's 0600 files.)

**Exceptions (named)**: none for keys — all eight keys live in
the shared file. The GSettings schema is retained solely as the
seed source above. Concurrency semantics: last-writer-wins PER
KEY (both sides re-read before writing one key), documented in
settingslib.

**Gate**: hook 1008 runs settingstest.py — lib round trips,
denylist, validation, corrupt fallback, daemon dispatch fns, and
a required two-user simulation (dclaw-agent writes → dclaw
reads/writes back → unrelated user `nobody` cannot open the
file). Validate script asserts the lib, the denylist and the
GUI/daemon wiring statically.
