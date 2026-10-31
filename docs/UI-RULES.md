# DclawOS UI Rules — the user's 10 iron rules, GTK realization

User directive (2026-10-31, verbatim, binding for every shipped
page): 「各个页面协调点，高质量丝滑一些，按大厂质感写前端，严守10条铁律」

The shipped frontend is GTK3 (`theme.py` + `theme.css`, Phase 3
module 7). The rules are written in web-CSS vocabulary; this file
is the binding translation: rule → exact user wording → GTK
implementation → platform deviation (honest, minimal). A rule is
never silently skipped: where GTK3 cannot express the web
primitive, the nearest equivalent is implemented in code and the
gap is written here.

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
  own 90 ms ramp. The model path itself is non-streaming
  (Phase 4 boundary, unchanged); this rule governs the reveal
  presentation of the completed reply.
- Status: CODE (P5-M5, chat window).

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
