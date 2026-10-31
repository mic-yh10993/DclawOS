# DclawOS UI Rules — the user's 10 iron rules, GTK realization

User directive (2026-10-31, verbatim, binding for every shipped
page): 「各个页面协调点，高质量丝滑一些，按大厂质感写前端，严守10条铁律」

Frontend reality (Phase 6 close-out, M6): the shipped frontend
is HYBRID. The base is GTK4+libadwaita (`theme.py` dual-generation
+ `theme-gtk4.css` / `theme-gtk4-dark.css`, Phase 6 M5); the three
high-interaction pages — model settings (M2), chat (M3),
dashboard (M4) — are local WebView pages (`model-settings.html` /
`chat.html` / `dashboard.html`, hosts on WebKitGTK 6.0 since M5)
where the rules are written in their native web form; the taskbar
(`dclawos-shell`) is the single named GTK3 exception (Wnck has no
GTK4 equivalent) and keeps the original GTK3 mapping below on
`theme.css` / `theme-dark.css`. The per-rule sections below are
the original Phase 5 GTK3 mapping, kept as the historical record
and as the taskbar's live mapping; the M5 addendum and the M6
close-out table at the end of this file are the current status of
record per surface. A rule is never silently skipped: where a
platform cannot express the web primitive, the nearest equivalent
is implemented in code and the gap is written here.

Status vocabulary: LANDED (in theme/apps + gate-asserted) ·
CODE (implemented in code, presentation-only, not gate-visible) ·
DEVIATION (platform cannot express it; nearest equivalent noted).

## Palette tokens (rule 1, shared by all rules)

GTK3 CSS has no variables; the tokens below are literal values,
kept identical in `theme.css`, the dark variant `theme-dark.css`,
and the embedded fallbacks in `theme.py`. Gate: the validate
script asserts these literals and the absence of `#000`.

| Token | Light | Dark | Used for |
|---|---|---|---|
| layer-0 (canvas) | `#f5f5f3` | `#0b0b0a` | window background |
| layer-1 (panel) | `#fcfcfb` | `#151515` | headerbar, cards, sidebar |
| layer-2 (raised) | `#fcfcfb` + inset hairline | `#1a1a19` | entries, popovers, tree rows hover |
| layer-3 (overlay) | `#f5f5f3` + shadow | `#20201e` | dialogs, menus, tooltips |
| ink (text) | `#1a1a19` | `#f5f5f3` | primary text |
| ink-dim | `#20201e` @ 65% | `#f5f5f3` @ 65% | secondary text |

Separation is ALWAYS a 1–3% lightness step between adjacent
layers (or an inset hairline in the neighbouring layer's tone),
never a drawn border in a contrasting colour.

## The 10 rules

### 1. 去边框：靠1%-3%微明度分层，禁纯黑#000。浅色#f5f5f3/#fcfcfb，暗色#0b0b0a/#151515/#1a1a19/#20201e。
- GTK: token table above; `border: none` / `border-color:
  transparent` on buttons, entries, frames, headerbar; hairlines
  via `box-shadow: inset 0 -1px <adjacent layer tone>`.
- `#000`/`#000000` appear nowhere in shipped CSS (gate-checked,
  comments stripped before the grep). Pure white `#fff` is not
  banned by the rule but the palette's lightest tone `#fcfcfb`
  replaces it on surfaces.
- Status: LANDED (P5-M5).

### 2. 输入框：聚焦不加outline，加深双环box-shadow造咬合感。
- GTK: GTK3 CSS has no `outline` property for entries; the
  equivalent of "no outline" is that focus does NOT change the
  border. Focus is expressed ONLY by the double ring:
  `box-shadow: inset 0 0 0 1px #3d5a80, 0 0 0 3px rgba(61,90,128,.28)`
  (deepened inner ring + soft outer ring — the "bite"). Applied
  to `entry:focus`, `textview:focus`, `spinbutton:focus`,
  `combobox:focus` (combobox ring lands on the whole widget —
  noted approximation).
- Status: LANDED (P5-M5), DEVIATION noted for combobox.

### 3. 暗色补偿：字重全降40（如400→360）防白字发胖。
- GTK: `theme-dark.css` requests the shifted weights per role:
  base 400→360, semibold 600→560, bold 700→660
  (`font-weight: 360/560/660`).
- DEVIATION: the shipped fonts (Noto Sans CJK / DejaVu) are not
  variable fonts; Pango rounds a requested weight to the nearest
  real face, so on those fonts 360 renders as the 400 face. The
  request is encoded correctly and takes full effect the moment
  a variable font is installed; it is not faked by shrinking
  size or thinning colour.
- Status: LANDED as encoded request (P5-M5), rendering deviation
  as above.

### 4. 流式打字：单字90ms从blur(6px)到清晰，缓动cubic-bezier(.215,.61,.355,1)，相邻字错开30ms晕开。
- GTK: chat replies are revealed by `theme.stream_reveal()`:
  characters are committed to the TextBuffer on a 30 ms stagger;
  each character passes 3 alpha steps across 90 ms
  (foreground alpha .25 → .6 → 1.0 via buffer tags) — alpha fade
  is the GTK equivalent of the blur ramp.
- The easing `cubic-bezier(.215,.61,.355,1)` is used verbatim in
  CSS transitions (revealer fades, feedback fade, rule 9/10).
- DEVIATION: GtkTextView cannot blur glyphs (no text blur
  primitive in GTK3); alpha ramp is the nearest equivalent.
  Very long replies batch characters per tick so total reveal
  stays ≤ ~4 s; within a batch every character still gets its
  own 90 ms ramp. (Phase 6 M6 note: the Phase 4 non-streaming
  boundary this paragraph originally cited ended with M3 — the
  model path is true streaming for openai/passthrough since M3,
  and the chat surface itself moved to the WebView page, where
  this rule is native; the alpha ramp above survives only as the
  legacy `stream_reveal` helper in theme.py, which no shipped
  window calls since M3. See the M6 close-out table.)
- Status: CODE (P5-M5, chat window; superseded per-surface by
  the M6 close-out table at the end of this file).

### 5. 行内图标：vertical-align:-0.125em对齐中心。
- GTK: GTK CSS has no `vertical-align` for images.
- Equivalent in code: `theme.inline_icon(image)` sets a 16 px
  pixel-size and `valign=CENTER` when the image sits in a
  horizontal box with text, and buttons containing image+label
  use `always-show-image` with the image at the text baseline
  side. DEVIATION: alignment is by widget box, not by em
  baseline; visually equivalent for the shipped icon sizes.
- Status: CODE (helper in theme.py, P5-M5); the shipped apps are
  almost entirely text-labelled, so application points are few
  and listed in PHASE5.md.

### 6. 数字防抖：加tabular-nums锁等宽。
- GTK: CSS `.numeric { font-feature-settings: "tnum" 1; }`
  (GTK 3.24 maps this to Pango font features). Applied to:
  dashboard counters, chat queue status line, monitor readouts,
  taskbar clock.
- Code fallback: `theme.apply_tnum(label)` sets the same feature
  via a Pango attribute list for labels built in code.
- Status: LANDED (CSS class) + CODE (helper), P5-M5.

### 7. 丝滑折叠：免JS算高，用Grid 0fr→1fr过渡。
- GTK: GTK3 has no CSS grid rows. The platform's layout-driven
  height animation is `Gtk.Revealer`
  (`transition-type=SLIDE_DOWN`, `transition-duration=250`,
  easing from the rule-4 curve): the layout engine animates the
  height — application code never measures or sets pixel
  heights, which is the rule's intent ("免JS算高").
- Applied to: settings registry sections (Skills / MCP /
  connectors / channels become revealer sections with header
  toggle buttons) and the dashboard takeover banner area.
- Status: CODE (P5-M5), DEVIATION: primitive differs (revealer,
  not grid rows); intent preserved.

### 8. 防孤字：正文加text-wrap:pretty。
- GTK: Pango/GtkLabel has no pretty/balanced wrapping mode.
- DEVIATION (platform lacks the feature): nearest equivalents
  applied — prose labels use `wrap-mode=WORD_CHAR`, a `.prose`
  class caps line length (max-width via code, ~68 chars) and
  centered short headings use `justify=CENTER`, which together
  minimise single-word last lines. Recorded as an approximation,
  not the feature.
- Status: DEVIATION with approximation (P5-M5).

### 9. 动效性格：拖拽用过阻尼弹簧(1200/80)零回弹咬死；点赞/对勾用欠阻尼(380/18)微弹。
- GTK: GTK3 has no spring solver and no CSS transforms.
- Mapping: any settle/drag motion uses the over-damped
  character — fast approach, ZERO overshoot — via
  `cubic-bezier(.215,.61,.355,1)` transitions (revealer,
  stack transitions set to 200 ms with that curve where the
  toolkit allows). The shipped apps contain NO drag
  interactions today; the rule is encoded as the motion token
  so the first drag surface inherits it.
- Under-damped micro-bounce (check/like): GTK3 cannot scale a
  widget, so `theme.flash_feedback()` performs the pulse in the
  opacity channel (1 → .55 → 1, 90 ms steps, one overshoot
  cycle) — the bounce's timing personality preserved, channel
  substituted. DEVIATION noted.
- Status: CODE (P5-M5).

### 10. 反馈自熄：复制对勾2.5s后自动淡出恢复。
- GTK: `theme.flash_feedback(widget, ok_text, restore_text)`:
  swaps the label to "✓ …", pulses once (rule 9), then after
  exactly 2500 ms (GLib timeout) fades (CSS opacity transition,
  rule-4 curve) and restores the original label.
- Applied to: chat「复制回答」button (copies the latest AI reply
  to the clipboard — the copy action this rule attaches to),
  settings secret-save status (timed clear), editor save status.
- Status: CODE (P5-M5).

## Where the system is applied

Every window app calls `theme.apply_theme()` (dashboard,
settings, chat, editor + compare window, approve, monitor,
takeover). The taskbar (`dclawos-shell`) applies it too since
P5-M5, scoped by the `.dclawos-taskbar` window class so the
dock's button padding/strut geometry is untouched (the Phase 3
exclusion was about padding, not palette); its colours and
typography follow the same tokens, and its clock uses `.numeric`.

Dark variant: GSettings `org.dclawos.settings ui-dark`
(default false) + a Settings → 通用 toggle; `theme.py` appends
`theme-dark.css` after the base sheet when set.

## Phase 6 M5 addendum — GTK4 mapping (additive record, no rule weakened)

The base toolkit moved from GTK3 to GTK4+libadwaita for every
window except the Wnck taskbar (docs/UI-FUNCTIONS.md). The 10
rules above are unchanged; only their GTK realization notes gain
a GTK4 column here:

- Sheets: `theme.py` is dual-generation. GTK4 processes load
  `theme-gtk4.css` / `theme-gtk4-dark.css` through
  `Gtk.StyleContext.add_provider_for_display`; the GTK3 taskbar
  keeps `theme.css` / `theme-dark.css` via the screen provider.
  Both generations carry the SAME palette tokens (rule 1) —
  parity is asserted by the validate script's M5 block, and
  `#000` remains absent from all four sheets.
- Rule 2 (focus double ring): the same box-shadow tokens are in
  the GTK4 sheets; GTK4 entry/textview focus states take them
  unchanged.
- Rule 3 (dark weights −40): `theme-gtk4-dark.css` carries the
  same 360/560/660 requests; the non-variable-font rounding
  deviation recorded for GTK3 applies identically.
- Rule 4 (streaming reveal): the GTK alpha-ramp deviation now
  applies only to `theme.stream_reveal`'s remaining legacy
  surface; the chat/dashboard pages are WebView-native since
  M3/M4 (recorded there) and the GTK4 windows have no streaming
  text surface.
- Rule 5 (inline icons): `theme.inline_icon` is version-agnostic
  (pixel-size + center alignment exist in both generations).
- Rule 6 (tabular-nums): `.numeric` + `theme.apply_tnum` work in
  both generations; GTK4's CSS parser accepts the
  `font-feature-settings` declaration as written.
- Rule 7 (fold): GtkRevealer remains the GTK4 primitive;
  `theme.make_section` has a GTK4 branch (set_child/append, the
  `flat` style class replacing the removed ReliefStyle).
- Rule 8 (pretty wrap approximation): GTK4 renamed the Label
  properties (`set_wrap` / `set_wrap_mode`); `theme.apply_prose`
  branches by loaded Gtk major version, same approximation.
- Rules 9/10 (motion + self-extinguishing feedback):
  `theme.flash_feedback` is widget-API-compatible with GTK4
  (label swap + opacity pulse + 2500 ms restore) and unchanged.

## Phase 6 M6 close-out — hybrid-reality status table (status of record)

Verified against the shipped code at the Phase 6 close (M5,
fa0378b): the three WebView pages' embedded CSS
(`model-settings.html`, `chat.html`, `dashboard.html`),
`theme-gtk4.css` / `theme-gtk4-dark.css` (GTK4 side),
`theme.css` / `theme-dark.css` (GTK3 taskbar side), and the
helpers in `theme.py`. Every cell carries exactly one of three
statuses: 逐字实现 (verbatim) / 近似 (approximation, gap
stated) / 不适用 (not applicable, reason stated). No rule is
deleted and no approximation is written as verbatim.

| # | Rule | WebView pages | GTK4+Adwaita native windows | GTK3 exception (taskbar, dclawos-shell) |
|---|---|---|---|---|
| 1 | 去边框、1–3% 微明度分层、禁 #000、指定调色板 | 逐字实现 — 三页 `:root`/`body.dark` 逐字使用浅色 #f5f5f3/#fcfcfb、暗色 #0b0b0a/#151515/#1a1a19/#20201e，`border:none` + inset 发丝线分层，三页均无 #000（门禁断言） | 逐字实现 — theme-gtk4.css / theme-gtk4-dark.css 与 GTK3 表同字面 token（validate M5 块断言双代际 token 一致 + 无 #000），分层同为 inset hairline/box-shadow 手法 | 逐字实现 — theme.css / theme-dark.css 同 token，经 `.dclawos-taskbar` 类作用域应用，几何（48px/strut）不动 |
| 2 | 聚焦无 outline、双环 box-shadow | 逐字实现 — 三页均为 `outline:none` + `box-shadow: inset 0 0 0 1px accent, 0 0 0 3px ring` 双环 | 近似 — GTK CSS 没有 outline 属性可关（无 outline 可禁），聚焦只以同一双环 box-shadow 表达、不改 border；差距：combobox 的环落在整个控件而非内嵌输入区（沿 Phase 5 注记） | 近似 — 同 GTK4 理由，旧映射（theme.css 同 token） |
| 3 | 暗色字重全降 40（400→360、600→560、700→660） | 逐字实现 — 三页 `body.dark` 逐字写 font-weight:360（正文）、560（标题/按钮）；三页未使用 700 字重角色，故无 660 落点 | 近似 — theme-gtk4-dark.css 已编码 360/560/660 请求值；差距：出厂字体（Noto Sans CJK / DejaVu）非可变字体，Pango 将请求取整到最近实字面，渲染数值不达 360/560/660（请求正确、效果待可变字体） | 近似 — 同上，旧映射（theme-dark.css 同请求值、同取整差距） |
| 4 | 流式打字 90ms blur(6px)→清晰、bezier(.215,.61,.355,1)、30ms 错开 | 聊天页：逐字实现（`@keyframes reveal` 90ms blur(6px)→clear、同缓动、30ms 错开、每 24 字循环使长 delta 立即开始）；看板页：逐字实现（限 llm 流式行，同 keyframe；非流式行按规则本义普通呈现）；模型设置页：不适用（页面无流式文本面，M2 台账已登记） | 不适用 — GTK4 窗口已无流式文本面（聊天/看板均已迁 WebView）；theme.py 仍保留 alpha-ramp 近似助手 `stream_reveal`（90ms 内 alpha .25→.6→1.0 代替 blur，GTK 无字形 blur 原语），但自 M3 起无任何窗口调用它，属遗留代码、不是现行落地 | 不适用 — 任务栏无流式文本面（时钟/状态为静态或整段更新标签） |
| 5 | 行内图标 vertical-align:-0.125em | 逐字实现 — 三页均有 `svg.ic{vertical-align:-0.125em}`（看板页为 `svg.ic,.ic`） | 近似 — GTK CSS 无 vertical-align；`theme.inline_icon` 以 16px pixel-size + valign CENTER 按控件盒对齐，差距：非 em 基线对齐；且 GTK 窗口几乎全为文本标签，应用点少 | 近似 — 同一双代际共用助手；任务栏基本无行内图标应用点 |
| 6 | tabular-nums 数字等宽 | 逐字实现 — 三页均有 `.num,.numeric{font-variant-numeric:tabular-nums; font-feature-settings:"tnum" 1}` | 逐字实现 — theme-gtk4.css `.numeric` 类同声明 + `theme.apply_tnum` 代码助手（监视窗状态读数等在用） | 逐字实现 — theme.css `.numeric` 同声明；任务栏时钟/电量标签经 `theme.apply_tnum` 应用（dclawos-shell 源码实证） |
| 7 | Grid 0fr→1fr 丝滑折叠（免算高） | 逐字实现 — 三页均用 `display:grid; grid-template-rows:0fr→1fr` 过渡（模型页卡片折叠、聊天页指派表单、看板页接管横幅） | 近似 — GTK 无 CSS grid rows；以 Gtk.Revealer（SLIDE_DOWN、布局驱动高度、应用代码不测高）实现同一意图（theme.make_section 的 GTK4 分支，设置页注册表分区在用），差距：原语不同（revealer ≠ grid rows） | 不适用 — 任务栏自身无折叠面；Revealer 近似映射在共用 theme.py 助手中（旧 GTK3 时代落在设置页，该页已迁 GTK4） |
| 8 | text-wrap:pretty 防孤字 | 逐字实现 — 三页均有 `p,.prose{text-wrap:pretty; max-width:68ch}` | 近似 — Pango/GtkLabel 无 pretty/balanced 换行模式；`theme.apply_prose` 以 wrap-mode WORD_CHAR + 约 68 字符行宽上限（+短标题居中）近似（设置页/监视窗说明文字在用），差距：近似而非该特性本身 | 近似 — 同一助手旧映射；任务栏正文量极少（标签类） |
| 9 | 动效性格：过阻尼 (1200/80) 零回弹 / 欠阻尼 (380/18) 微弹 | 近似 — Web CSS 同样没有弹簧求解器，数值弹簧参数 (1200/80、380/18) 在任何 CSS 面都无法逐字落地；三页以 cubic-bezier(.215,.61,.355,1) 表达过阻尼 settle（零回弹）、以 cubic-bezier(.34,1.8,.64,1) 的 .38s scale pulse 表达欠阻尼微弹，差距：性格等价、参数非逐字 | 近似 — GTK 无弹簧求解器且不能缩放控件；过阻尼侧用同一 bezier 过渡，欠阻尼侧由 `theme.flash_feedback` 以 opacity 脉冲（1→.55→1，90ms 步、一次过冲）替代 scale 微弹（编辑器保存反馈在用），差距：通道替换（opacity 代 scale） | 近似 — 旧映射同上（共用 theme.py 助手与 theme.css 过渡 token）；任务栏内无拖拽/点赞类应用点，规则以主题 token 形式存在 |
| 10 | 反馈 2.5s 自熄 | 逐字实现 — 三页反馈（toast / 复制对勾）均由 2500ms 定时自熄（模型页 toast、聊天页复制对勾+toast、看板页 toast） | 逐字实现 — 时长逐字：`theme.flash_feedback` 精确 2500ms 后恢复（编辑器保存状态在用）；设置页密钥保存状态以自有 GLib.timeout_add(2500) 清除（其伴随脉冲属铁律 9 的近似，不影响本条时长口径） | 不适用 — 任务栏无复制/保存类反馈面 |

Reading note: the LANDED / CODE / DEVIATION statuses in the
per-rule sections above describe the Phase 5 GTK3-only reality
they were written in. Where they differ from this table, this
table governs: it is the only place that states all three
surfaces (WebView / GTK4 / GTK3 exception) side by side, and it
was re-verified line by line against the code at M6.
