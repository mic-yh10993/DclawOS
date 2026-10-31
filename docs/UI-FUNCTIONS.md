# DclawOS UI 功能对照表 — Phase 6 M5（GTK4 迁移门禁）

本表是 M5 的门禁先行交付：先把功能全集从仓库实证拼出，再逐窗口
施工。任何一行没有落点即 M5 未完成。

## 实证来源（不凭印象）

- 阶段台账：`docs/PHASE4-GAP.md`（A/B/C 全集）、`docs/PHASE5.md`
  （WS1 渠道 / WS2 自装工具 / WS3 UI 铁律）、`docs/PHASE6.md`
  （M1 推送总线、M2 模型设置、M3 聊天流式、M4 看板+任务栏状态+
  审批覆盖层）。
- 窗口二进制文件头 docstring 与源码：`usr/local/bin/dclawos-*`。
- 设置键：`settingslib.KEY_SPECS`（8 键，逐行列）。
- 注册表类别：`registrylib`（skills / mcp_servers / connectors /
  channels / providers / model_config，逐行列）。
- 工具：`toolslib.TOOL_SCHEMAS`（27 个，逐个列一行）。
- 桥动词：`bridgeprotocol.SURFACE_COMMANDS`（host/settings/chat/
  dashboard 四面，逐面列）。
- 密钥存储：broker `write-secret` 0600 文件（agentlib /
  dclawos-privileged `op_write_secret`）。

## 状态词表（validate 脚本按此做负向断言）

允许的状态只有以下五种；出现「待映射」「未映射」字样的行即门禁
失败（脚本对全文做负向 grep，词表外状态视为未收口）：

- `已映射-GTK4`：功能落在已迁 GTK4+libadwaita 的窗口/页。
- `已映射-WebView`：功能落在 WebView 页（chat/dashboard/model），
  宿主已迁 GTK4+WebKitGTK 6.0，页面文件与桥协议不变。
- `GTK3例外-Wnck`：仅任务栏；理由=Wnck（gir1.2-wnck-3.0）是
  GTK3 生态、GTK4 无等价物，窗口切换/激活依赖它；保留 GTK3，
  只对齐调色板，几何（48px/strut）不动。
- `不适用-非GTK`：CLI / Calamares / 守护进程等非 GTK 面，本
  模块不动，在此登记以证明盘点过。
- `未实现-已排队`：功能本身尚未实现（非迁移问题），落点写明
  排队阶段；不许用迁移假装补齐。

## apt 核实（Debian Bookworm，packages.debian.org/bookworm 实查，
## 2026-10-31；本机为 Ubuntu 源、不能代替 Bookworm 核实）

| 包 | Bookworm 版本（实查） | 结论 |
|---|---|---|
| gir1.2-gtk-4.0 | 4.8.3+ds-2+deb12u1 | 存在，进包清单 |
| gir1.2-adw-1 | 1.2.2-1（libadwaita-1-0 ≥1.2~beta） | 存在，进包清单 |
| libadwaita-1-0 | 1.2.x（gir1.2-adw-1 的依赖） | 随 gir 依赖带入，显式列入 |
| gir1.2-gtksource-5 | 5.6.x（libgtksourceview-5-0 ≥5.6） | 存在，进包清单，代 gir1.2-gtksource-4 |
| gir1.2-webkit-6.0 | 2.50.6-1~deb12u2（bookworm-security；依赖 gir1.2-gtk-4.0/libwebkitgtk-6.0-4） | **存在**——WebView 宿主真迁 GTK4+WebKitGTK 6.0，不记例外；代 gir1.2-webkit2-4.1 |
| gir1.2-wnck-3.0 | GTK3 生态（libwnck-3） | 保留（任务栏例外 + dclawos-gui CLI） |

API 代际约束（按 Bookworm 版本写代码，不用新版才有的 API）：
GTK 4.8 / libadwaita 1.2 / GtkSourceView 5.6 / WebKitGTK 6.0。
禁用：Gtk.FileDialog / Gtk.AlertDialog（4.10+）、
Adw.ToolbarView / Adw.NavigationSplitView（1.4+）、
Adw.ViewSwitcherBar 的新 API。对话框统一用 GTK4 仍保留的
Gtk.Dialog / Gtk.FileChooserDialog / Gtk.MessageDialog +
`response` 信号 + 嵌套 GLib.MainLoop（`run_dialog()` 助手，
GTK4 已删除 `.run()`），文件选择用 `get_file().get_path()`
（GTK4 已删除 `get_filename()`）。

## 功能对照表

| 功能 | 来源（阶段/文件） | 新 UI 落点（窗口/页/动词） | 状态 |
|---|---|---|---|
| 设置-通用：窗口模式 window-mode（fullscreen/windowed 下拉直写共享设置文件） | P2-M3 / dclawos-settings build_general_panel；settingslib KEY_SPECS | dclawos-settings（GTK4/Adw）通用页 labeled_row+ComboBoxText | 已映射-GTK4 |
| 设置-通用：agent-full-access 完全访问开关（人类专属键） | P3-M4 / build_general_panel；KEY_SPECS | dclawos-settings 通用页 Switch | 已映射-GTK4 |
| 设置-通用：ui-dark 深色界面开关 | P5-M5 / build_general_panel；KEY_SPECS | dclawos-settings 通用页 Switch | 已映射-GTK4 |
| 设置键 taskbar-autohide（保留键：任务栏未消费、无 GUI 控件，仅 GSettings 种子迁移与 settings_get/set 工具面） | settingslib KEY_SPECS；gschema 注释「reserved」 | 无窗口控件（现状即如此）；经桥 settings.get/set 与 agent 工具 settings_get/set 读写，落点=设置宿主桥+守护进程 | 已映射-GTK4 |
| 设置键 ai-provider（全局默认供应商） | KEY_SPECS；build_ai_panel / 模型页 pins | dclawos-settings AI与模型页 + 模型设置 WebView 页（model.config） | 已映射-GTK4 |
| 设置键 ai-model（全局默认模型） | KEY_SPECS；同上 | dclawos-settings AI与模型页 + 模型设置 WebView 页 | 已映射-GTK4 |
| 设置键 routing-mode（auto/manual） | KEY_SPECS；build_ai_panel | dclawos-settings AI与模型页 ComboBoxText | 已映射-GTK4 |
| 设置键 chaos-enabled（人类专属、agent 被 broker denylist 拒绝） | KEY_SPECS；PRIVILEGE-MODEL / dclawos-privileged | 无窗口开关（现状即如此，靠引擎 env/设置文件）；落点=特权代理 denylist（非 GTK） | 不适用-非GTK |
| 设置-AI与模型（旧原生面板）：目录驱动供应商/模型下拉、目录缺失回退自由文本、路由模式、焦点回读 | P2-M3/M4 / build_ai_panel、load_catalog、_refresh_from_store | dclawos-settings AI与模型页（GTK4）逐项保留，与模型 WebView 页并存（M2 台账既定） | 已映射-GTK4 |
| 设置-模型设置 WebView 页：供应商目录合并视图 model.catalog | P6-M2 / build_model_panel、_model_dispatch；model-settings.html | dclawos-settings 模型设置页（GTK4+WebKit6 宿主，页面不变） | 已映射-WebView |
| 模型页动词：model.provider.save / delete（自定义供应商 CRUD，内置拒绝） | P6-M2 / registrylib providers 类别 | 同上页，宿主 _model_dispatch 不变 | 已映射-WebView |
| 模型页动词：model.provider.override / reset（内置供应商覆盖层） | P6-M2 / registrylib model_config.provider_overrides | 同上 | 已映射-WebView |
| 模型页动词：model.config.get / save（路由 strategy/weights 16 因子/modePack/SLA/pins/fallback/budget 等全字段，校验后落盘） | P6-M2 / registrylib.normalize_model_config | 同上 | 已映射-WebView |
| 模型页动词：model.health（质量/熔断/限流状态，lockout 跨进程不可得如实报） | P6-M2 / modellib | 同上 | 已映射-WebView |
| 模型页动词：model.test（连通性测试；无密钥短路、绝不编造成功） | P6-M2 / llmlib.test_connection | 同上（本地 mock 验证口径不变） | 已映射-WebView |
| 模型页动词：model.usage（用量账本，null 成本单独计不算 0） | P6-M2 / usagelib | 同上 | 已映射-WebView |
| 桥 settings 面其余动词：settings.get / settings.set / registry.list / hello / ping | P6-M1/M2 / bridgeprotocol SURFACE_COMMANDS[settings] | dclawos-settings 宿主（GTK4）_on_bridge_message 不变 | 已映射-GTK4 |
| 桥 secret.set（只写密钥，llm-<id>，经审批+broker write-secret，回执不回显） | P6-M1/M2 / bridgeprotocol（secret_params=True 唯一入口） | dclawos-settings 宿主 _model_dispatch secret.set 不变 | 已映射-GTK4 |
| 设置-Skills与MCP 页：skills 列表/添加/删除/切换启用（许可证逐项确认） | P2-M9 / build_skills_panel、_registry_section（规则7 Revealer 折叠） | dclawos-settings Skills与MCP 页（GTK4，theme.make_section 的 GTK4 分支） | 已映射-GTK4 |
| 设置-Skills与MCP 页：mcp_servers 列表/添加/删除/切换启用（env 不在此编辑、secret:<名> 引用） | P2-M9 / registrylib mcp_servers | 同上页 | 已映射-GTK4 |
| 设置-Skills与MCP 页：connectors 列表/添加/删除/切换启用 | P2-M9 / registrylib connectors | 同上页 | 已映射-GTK4 |
| 设置-渠道页：channels 列表/添加/删除/切换启用/更新令牌与白名单（telegram/discord/slack/matrix；令牌只写 channel-<id>） | P5-WS1 / build_channels_panel、registrylib channels | dclawos-settings 渠道页（GTK4）逐项保留 | 已映射-GTK4 |
| 设置-密钥页：名称+值只写保存（审批或完全访问自动过、0600 文件、永不回显） | P3-M2 / build_secrets_panel、_store_secret | dclawos-settings 密钥页（GTK4） | 已映射-GTK4 |
| 注册表类别 providers / model_config 的 GUI 落点（同模型页行，此处登记类别全集核对） | P6-M2 / registrylib 类别全集 | dclawos-settings 模型设置 WebView 页 | 已映射-WebView |
| 设置窗口外壳：侧栏页切换（Stack+StackSidebar 6 页）、焦点回读共享文件、首启 GSettings 种子迁移 | P2-M3/P5-M7 / SettingsApp、migrate_gsettings_once | dclawos-settings（Adw.ApplicationWindow + Gtk.Stack/StackSidebar 视图切换） | 已映射-GTK4 |
| 编辑器：打开/保存（含未命名另存、脏标记标题、未保存确认 保存/放弃/取消） | P2-M8 / dclawos-editor EditorApp | dclawos-editor（GTK4+GtkSourceView5）FileChooser 对话框（response 信号版） | 已映射-GTK4 |
| 编辑器：语法高亮/行号/当前行/自动缩进/语言猜测/样式方案 | P2-M8 / make_source_view、set_language（EDITOR-DECISION） | dclawos-editor GtkSource 5 Buffer/View（API 同名沿用） | 已映射-GTK4 |
| 编辑器：查找/上一个/下一个（SearchContext 高亮） | P2-M8+P3-M6 / find() | dclawos-editor 查找行（GtkSource 5 SearchContext） | 已映射-GTK4 |
| 编辑器：替换当前/全部替换（纯文本语义、防死循环） | P3-M6 / on_replace、on_replace_all | dclawos-editor 替换行 | 已映射-GTK4 |
| 编辑器：跳到行（越界报范围） | P3-M6 / on_goto_line | dclawos-editor 跳行行 | 已映射-GTK4 |
| 编辑器：撤销/重做按钮 | P3-M6 / on_undo、on_redo（GtkSource.Buffer 原生） | dclawos-editor 工具栏 | 已映射-GTK4 |
| 编辑器：在终端打开所在目录（Q7 不内嵌终端、开系统 xterm） | P2-M8 / on_terminal | dclawos-editor 工具栏按钮（逻辑不变） | 已映射-GTK4 |
| 编辑器：agent 修改文件侧栏（modifiedfiles 契约、双击打开、2s 刷新） | P2-M8 / refresh_sidebar、on_sidebar_open | dclawos-editor 左侧栏（GTK4 Paned start child） | 已映射-GTK4 |
| 编辑器：diff 对比窗（双 GtkSourceView 只读、滚动同步、行级+字符级高亮、对比选择对话框含浏览） | P2-M8+P3-M6 / CompareWindow、CompareDialog | dclawos-editor CompareWindow（GTK4）+ CompareDialog（GTK4 Dialog） | 已映射-GTK4 |
| 编辑器：保存成功反馈 2.5s 自熄（铁律10） | P5-M5 / save_file → theme.flash_feedback | dclawos-editor 状态标签（theme GTK4 分支同语义） | 已映射-GTK4 |
| 监视窗：agent 进程列表（ps 只读）+ AI 状态（停止标志）总览 | P2-M7 / dclawos-monitor MonitorApp.refresh | dclawos-monitor（GTK4/Adw） | 已映射-GTK4 |
| 监视窗：叫停 AI（协作停止标志）/ 恢复 AI / 强制结束 agent 进程（确认对话框） | P2-M7（Q1 监视+干预）/ on_stop、on_resume、on_kill | dclawos-monitor（GTK4）三按钮逐项保留；确认对话框=response 信号版 MessageDialog | 已映射-GTK4 |
| 监视窗：推送刷新（M1 事件总线相关事件触发、无显示定时器） | P6-M1 / _on_bus_event_thread、refresh_from_bus | dclawos-monitor（GTK4）EventSubscriber 不变 | 已映射-GTK4 |
| 接管窗：请求列表（种类/内容/状态/时间、隐藏 id 列）+ 详情 | P2-M6 / dclawos-takeover TakeoverApp | dclawos-takeover（GTK4/Adw） | 已映射-GTK4 |
| 接管窗：回填 成功/失败/取消 + 会话标识输入（只回结果+句柄、绝不回凭证明文，Q2 红线；凭据类附加键由 takeoverlib 丢弃） | P2-M6（Q2）/ on_resolve、takeoverlib.write_result | dclawos-takeover（GTK4）按钮与语义不变 | 已映射-GTK4 |
| 接管窗：请求列表定时刷新（1s POLL——M1 推送化未覆盖此窗，现状保留并在此登记） | P2-M6 / GLib.timeout_add(POLL_MS) | dclawos-takeover（GTK4）保留现状；推送化改造不在 M5 授权内 | 已映射-GTK4 |
| 审批窗（独立）：待审批票据列表（操作/摘要/时间，只显示摘要不显示密钥） | P3-M1 / dclawos-approve ApproveApp、list_pending | dclawos-approve（GTK4/Adw）保留并迁移 | 已映射-GTK4 |
| 审批窗（独立）：批准/拒绝（写 status/approved_by=human-gui/approved_at，与 chatlib.apply_verdict 字段一致） | P3-M1 / on_verdict | dclawos-approve（GTK4）逐项保留 | 已映射-GTK4 |
| 审批窗的现行调用关系（盘点结论）：主路径已被 M3 聊天内联卡片与 M4 看板覆盖层覆盖；但 dashboardlib.LAUNCH_TARGETS 仍有 approve→dclawos-approve 启动项，且 agentlib 台账称其为生产审批路径、设置页密钥/渠道保存的审批靠请求方轮询票据状态——故它是仍在用的兜底入口，不是死代码 | P6-M4 / dashboardlib LAUNCH_TARGETS；agentlib.request_approval | 保留二进制并迁 GTK4（不删除、不降级为取代） | 已映射-GTK4 |
| 聊天页：会话列表/切换/新建（convlib 转录）+ 历史渲染 | P6-M3 / chat.html、chatlib（chat.state/switch/new/history） | dclawos-chat 宿主（GTK4+WebKit6）+ chat.html 不变 | 已映射-WebView |
| 聊天页：发送消息建 llm 任务、真流式气泡（铁律4 原生 blur 晕开）、最终回复对账替换不重复 | P6-M3 / chat.send、llmlib.call_stream、守护进程 delta 推送 | 同上（桥与页面不变、宿主换代） | 已映射-WebView |
| 聊天页：工具步骤卡（command/output 事件内联）+ 实时输出 | P6-M1/M3 / chatlib.handle_bus_event 主机侧过滤 | 同上 | 已映射-WebView |
| 聊天页：内联审批卡 批准/拒绝（chat.approve/deny → apply_verdict 唯一写路径） | P6-M3 / chatlib | 同上 | 已映射-WebView |
| 聊天页：停止按钮（协作停止）/ 斜杠命令（/run /read /write /skill /mcp /connector /route /subagent /takeover /stop /help）/ 指派任务折叠表单 | P6-M3 / chat.stop、chatlib.parse_command、chat.assign | 同上 | 已映射-WebView |
| 聊天页：页眉模型切换（合并目录→全局默认）/ 未配置诚实横幅 / 回复复制按钮 2.5s 对勾反馈 | P6-M3 / chat.model.set、chat.copy（宿主剪贴板） | 同上；宿主剪贴板改 Gdk 4 Clipboard API（Gtk.Clipboard 已删除） | 已映射-WebView |
| 聊天页：启动兄弟应用 chat.launch | P6-M3 / chatlib launch 表 | dclawos-chat 宿主（GTK4） | 已映射-WebView |
| 看板页：事件时间线（启动读一次历史、其后只靠总线推送、id/指纹双重去重） | P6-M4 / dashboard.html、dashboardlib.DashboardHost | dclawos-dashboard 宿主（GTK4+WebKit6）+ dashboard.html 不变 | 已映射-WebView |
| 看板页：计数区（运行/等待/完成/失败 + token/成本，null 成本单独计）由宿主事件驱动重算推送 | P6-M4 / dashboard.state、dashboard.counts | 同上 | 已映射-WebView |
| 看板页：接管横幅（Grid 折叠、点击启动 dclawos-takeover） | P6-M4 / dashboard.launch | 同上 | 已映射-WebView |
| 看板页：审批覆盖层（分级风险带、参数主机侧脱敏、批准/拒绝两按钮） | P6-M4 / dashboard.approve/deny → chatlib.apply_verdict | 同上 | 已映射-WebView |
| 看板宿主：启动写「看板已启动」事件（其他工具可能依赖） | P2-M2 沿用 / append_event | dclawos-dashboard 宿主（GTK4）保留 | 已映射-GTK4 |
| 任务栏：槽位切换 AI/设置/Chromium/终端（Wnck 窗口查找/激活/最大化、窗口模式） | P2-M1 / dclawos-shell Taskbar、find_window_for_slot | dclawos-shell 保留 GTK3（Wnck 无 GTK4 版） | GTK3例外-Wnck |
| 任务栏：48px 高 + _NET_WM_STRUT_PARTIAL 保留区 + 时钟/电量/版本标签 | P2-M1 / TASKBAR_HEIGHT、on_realize | dclawos-shell 几何逐字不动、只确认调色板对齐 | GTK3例外-Wnck |
| 任务栏：agent 四态指示器（需要你>等待批准>思考中>空闲、点击开看板、等待态呼吸仅动画定时器） | P6-M4 / reduce_shell_state、initial_shell_state | dclawos-shell（GTK3 例外窗内既有功能，M5 不动其代码） | GTK3例外-Wnck |
| 主题：浅/暗调色板（铁律1 禁 #000、1–3% 明度分层）、聚焦双环（铁律2）、暗色字重 −40（铁律3）、tabular-nums（铁律6） | P5-M5 / theme.py、theme.css、theme-dark.css、docs/UI-RULES.md | GTK4 版主题 theme-gtk4.css / theme-gtk4-dark.css（同 token）；GTK3 例外窗继续用旧 theme.css 且 token 一致（同一文件双门禁核对） | 已映射-GTK4 |
| 主题代码助手：flash_feedback（铁律9/10 脉冲+2.5s 自熄）、apply_tnum、apply_prose（铁律8 近似）、inline_icon（铁律5 近似）、make_section（铁律7 Revealer 折叠）、stream_reveal（铁律4 GTK alpha 版；现仅旧面引用） | P5-M5 / theme.py 各 helper | theme.py 双代际分支：GTK4 进程走 GTK4 API、GTK3 进程（任务栏）走旧 API，语义与签名不变 | 已映射-GTK4 |
| 工具 shell（argv 执行、实时 output 事件） | P4-A5/C5 / toolslib.TOOL_SCHEMAS；守护进程 _run_shell | 无独立窗口：聊天页步骤卡 + 看板时间线呈现（宿主已迁） | 已映射-WebView |
| 工具 read_file | P4-A5 / toolslib | 无独立窗口：聊天指派/守护进程执行，结果回聊天页与看板 | 已映射-WebView |
| 工具 write_file | P4-A5 / toolslib | 无独立窗口：同上 | 已映射-WebView |
| 工具 edit_file（精确单处替换） | P4-C5 / toolslib | 无独立窗口：同上 | 已映射-WebView |
| 工具 list_files | P4-A5 / toolslib | 无独立窗口：同上 | 已映射-WebView |
| 工具 process（后台进程 start/poll/log/kill；其实时输出为 M1 已登记 DEFERRED-NAMED） | P4-C5 / toolslib；PHASE6 M1 台账 | 无独立窗口：同上呈现；实时输出边界维持 M1 登记不假装 | 已映射-WebView |
| 工具 web_fetch | P4-C5 / toolslib | 无独立窗口：同上 | 已映射-WebView |
| 工具 service_status / service_restart（broker） | P4-A5 / toolslib | 无独立窗口：审批经聊天内联卡/看板覆盖层/独立审批窗 | 已映射-WebView |
| 工具 package_install（broker） | P4-A5 / toolslib | 无独立窗口：审批同上 | 已映射-WebView |
| 工具 route_decide | P4-A5 / toolslib | 无独立窗口：聊天 /route 与守护进程调用 | 已映射-WebView |
| 工具 mcp_call | P4-A5 / toolslib | 无独立窗口：MCP 服务器在设置-Skills与MCP 页管理（已映射-GTK4 行） | 已映射-GTK4 |
| 工具 skill_run | P4-A5 / toolslib | 无独立窗口：skills 在设置页管理 | 已映射-GTK4 |
| 工具 subagent（子代理、独立会话/可选 pin） | P4-C1 / toolslib；subagent_pin 在模型页 | 无独立窗口：聊天 /subagent 与模型页 pin | 已映射-WebView |
| 工具 takeover_request | P4-A5 / toolslib | dclawos-takeover 接管窗承接 | 已映射-GTK4 |
| 工具 memory_save | P4-C3 / toolslib | 无独立窗口：守护进程文件记忆、聊天上下文组装 | 已映射-WebView |
| 工具 memory_search | P4-C3 / toolslib | 无独立窗口：同上 | 已映射-WebView |
| 工具 cron（add/list/remove 定时任务） | P4-C2 / toolslib、cronlib | 无独立窗口：守护进程调度、结果回事件流（看板页） | 已映射-WebView |
| 工具 connector_send | P4-C4 / toolslib | 无独立窗口：连接器在设置-Skills与MCP 页管理 | 已映射-GTK4 |
| 工具 registry_list（env 脱敏列注册表） | P5-WS2 / toolslib | 无独立窗口：与设置页注册表管理同数据源 | 已映射-GTK4 |
| 工具 skill_install（自装 skill、license 强制、校验失败不注册） | P5-WS2 / toolslib、provisioninglib | 无独立窗口：结果落设置-Skills与MCP 页列表 | 已映射-GTK4 |
| 工具 skill_remove（删注册项+自装载荷） | P5-WS2 / toolslib、provisioninglib | 无独立窗口：结果落设置-Skills与MCP 页列表 | 已映射-GTK4 |
| 工具 mcp_add（握手校验后才注册） | P5-WS2 / toolslib、provisioninglib | 无独立窗口：结果落设置-Skills与MCP 页列表 | 已映射-GTK4 |
| 工具 mcp_remove | P5-WS2 / toolslib、provisioninglib | 无独立窗口：结果落设置-Skills与MCP 页列表 | 已映射-GTK4 |
| 工具 settings_get（读共享设置文件，省 key 读全部） | P5-M7 / toolslib、settingslib | 无独立窗口：设置宿主同文件对读（焦点回读行已映射） | 已映射-GTK4 |
| 工具 settings_set（改共享设置；人类专属键拒绝；经 broker+审批） | P5-M7 / toolslib、settingslib | 无独立窗口：设置窗口焦点回读即见新值 | 已映射-GTK4 |
| 工具 channel_manage（渠道增删/启停/白名单；令牌永不经此工具） | P5-WS2 / toolslib | 无独立窗口：结果落设置-渠道页列表 | 已映射-GTK4 |
| 桥 host 面：hello / ping（版本协商） | P6-M1 / bridgeprotocol | 三宿主（settings/chat/dashboard，GTK4）共用 | 已映射-GTK4 |
| 渠道适配器 telegram（Bot API 长轮询）/ discord（REST 轮询、网关 WS 未做为已登记取舍）/ slack（Socket Mode、无历史回补）/ matrix（无 E2EE） | P5-WS1 / channel_*.py、channellib | 无窗口（守护进程运行时）：管理面=设置-渠道页（已映射行） | 不适用-非GTK |
| 渠道 WhatsApp | P5-WS1 / PHASE5 台账 BLOCKED-NAMED（公网 webhook vs Baileys 风险待拍板） | 无落点可迁：维持具名阻塞、M5 不拍板不假装 | 未实现-已排队 |
| 渠道 IRC / Mattermost | P5-WS1 / PHASE5 台账「可行延后」 | 无落点可迁：维持延后登记 | 未实现-已排队 |
| 范围授权（本次任务允许，scoped approval） | P6-M4 台账：票据模型无任务级授权载体、未建按钮 | 落点=P0/易用性阶段（需先扩票据模型：范围授权记录 + broker/agentlib 执行时认账） | 未实现-已排队 |
| 安装器（Calamares 分区/引导/用户，live 与装后权限区分） | P3-M5 / dclawos-installer（shell 包装 calamares） | 非 GTK（Qt/QML Calamares），M5 不动 | 不适用-非GTK |
| dclawos-secret（密钥 CLI：审批+broker 写密钥） | P3-M2 / dclawos-secret | CLI 非 GTK，不动 | 不适用-非GTK |
| dclawos-gui（Wnck 窗口列举/激活 + xdotool 键鼠 CLI，供 agent GUI 操作） | P2-M? / dclawos-gui（Wnck 3.0） | CLI 非窗口；且 Wnck 同任务栏理由，不动 | 不适用-非GTK |
| dclawos-agent / dclawos-agent-ctl / dclawos-agent-run（守护进程与启停 CLI） | P3-M1/P4 / bin | 非 GTK，不动；其状态呈现面=监视窗/看板（已映射行） | 不适用-非GTK |
| dclawos-privileged（特权代理 broker、Unix socket）/ dclawos-ticket（票据 CLI、测试用） | P2-M5/P3-M1 / bin | 非 GTK，不动 | 不适用-非GTK |
| dclawos-route / dclawos-llm（路由与 LLM 引擎 CLI） | P2-M4/P4-A1 / bin | 非 GTK，不动；本地 mock 验证口径不变 | 不适用-非GTK |
| dclawos-log-event / dclawos-connector-local / dclawos-takeover-request（CLI/本地连接器） | P2 / bin | 非 GTK，不动 | 不适用-非GTK |

## 逐窗口处置结论（与 PHASE6 M5 台账互相引用）

| 窗口 | 处置 | 一句话理由 |
|---|---|---|
| dclawos-settings | 迁 GTK4+Adwaita（Adw.ApplicationWindow），模型页宿主迁 WebKitGTK 6.0 | Bookworm 三包齐备（见 apt 核实表） |
| dclawos-editor | 迁 GTK4 + GtkSourceView 5 | EDITOR-DECISION 功能全保留；GtkSource 5 为 GTK4 版 |
| dclawos-monitor | 迁 GTK4+Adwaita | 纯 GTK 面、无特殊依赖 |
| dclawos-takeover | 迁 GTK4+Adwaita | 纯 GTK 面；Q2 语义不变 |
| dclawos-approve | 迁 GTK4+Adwaita（保留） | 仍是 dashboard 启动表与密钥保存审批的兜底入口（见表中盘点行） |
| dclawos-chat | 宿主迁 GTK4 + WebKitGTK 6.0 | gir1.2-webkit-6.0 在 Bookworm 存在且桥 API 等价（UserContentManager/脚本消息/evaluate_javascript），页面与 chatlib 不变 |
| dclawos-dashboard | 宿主迁 GTK4 + WebKitGTK 6.0 | 同上，页面与 dashboardlib 不变 |
| dclawos-shell | **GTK3 例外**（不迁） | Wnck 无 GTK4 版，窗口切换依赖它；几何不动、调色板已对齐 |
| dclawos-gui | 不适用-非GTK（CLI，且同 Wnck） | 命令行工具非窗口 |
| dclawos-secret | 不适用-非GTK | CLI |
| dclawos-installer | 不适用-非GTK | Calamares（Qt/QML）包装脚本 |

## GTK3 例外清单（机器可核对，validate 脚本逐项比对）

```
GTK3-EXCEPTION: dclawos-shell (Wnck)
GTK3-PIN: dclawos-gui (Wnck CLI — M5 共存 pin，非窗口)
```

清单规则：validate 脚本断言——bin 中含 `require_version("Gtk", "3.0")`
的文件必须恰为 {dclawos-shell, dclawos-gui}：前者是 Wnck 例外
窗口，后者是 Wnck CLI 在双代际 typelib 共存下必须显式 pin 的
 Gtk/Gdk 3.0（M5 实测：不 pin 时 Gdk 先解析到 4.0 再与 Gtk 3.0
冲突，shell/gui 双双起不来；pin 后双双构造冒烟通过）。清单外
出现 Gtk 3.0 或清单内文件不再是 Gtk 3.0，两边都失败。
WebKit 例外：无（WebKit2 4.1 应从所有宿主与包清单中消失，
validate 负向断言）。
