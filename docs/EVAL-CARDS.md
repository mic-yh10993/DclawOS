# DclawOS 复用评估卡（草案，第 1 步，2026-10-29）

> 本文件已获用户确认（见下方确认记录），代码按此卡实施。
> 确认记录（用户 2026-10-29 20:38 邮件“回复: DclawOS 复用评估卡（请确认）”）：评估卡已确认，可以直接开始写代码，自主调试直到构建成功。Q1 监视 + 干预/叫停都要实现；Q2 按卡片安全默认值（接管回填只回成功/失败 + 会话句柄，绝不回填凭证明文）；Q3 skills 与 mcp 统一，claw 的 skill、mcp 等各项管理全部集成进 GUI 设置，不走 TUI；Q4 随映像编译；Q5 Chromium 占任务栏位；Q6 按助手思路（自有 GSettings schema，不另补检索 gsettings-desktop-schemas）；Q7 按助手思路（编辑器不内嵌终端，用系统终端应用）。另：openclaw 不直接安装，要重新编译成系统级；GUI 搞定一切；随时唤起聊天页面；随时右键提问。
> 依据：用户 2026-10-29 18:48 批复（大方向正确 + 变更清单）；第 0 步检索报告 `RESEARCH.md`。
> 行号均为本轮复核后的实测值。`RESEARCH.md` 两处行号漂移已纠正：
> `gnome-shell/js/ui/altTab.js` 的 AppSwitcher 实际在 696 行（非 76 行，64 行为 AppSwitcherPopup）；
> `gnome-shell/js/ui/appDisplay.js` 的 `_redisplay` 实际在 1078 行（非 1400 行）。
> 成本估计为定性估计（小 / 中 / 大），仅供排期参考，不是承诺。
> 许可证基线（用户已定）：全部 GPL。MIT 代码（openclaw、omniroute、vscode）保留版权声明即可并入 GPL 项目；
> GPL-2.0-or-later 代码（三个 GNOME 仓库）按 GPL 分发；各产品的商标与产品名另算，须改名换标；
> 捆绑依赖的许可证按文件头逐项核对（两仓库均有 THIRD_PARTY_NOTICES）。

## 1. 底部任务栏 + 全屏切换 + 永远置顶

用户定：任务栏自己实现；不要 Firefox（Chromium 为浏览器，作普通应用）；
切换应用类似安卓全屏窗口；任务栏永远置顶；设置可选窗口模式。

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| TaskbarShell（底部任务栏壳：应用按钮、切换、置顶容器） | 无等价实现。检索命令：`grep -rni "taskbar\|task_bar\|task-bar" gnome-shell/js/ui/`，仅命中 skip_taskbar 标志位（`altTab.js:52,59-60`、`appMenu.js:276`、`windowAttentionHandler.js:28`），无任务栏组件 | 自写 | 参考库中无等价实现（检索为证）；GNOME 只有顶栏，无底部任务栏 | 自写：中（UI 壳 + 状态订阅，需接 §1 其余行与应用发现） |
| 任务栏状态区聚合（电源 / 网络 / 音量 / 监控图标） | 适配：`gnome-shell/js/ui/panel.js:445`（Panel 类）、`:470`（statusArea）；`js/ui/status/system.js:26`（PowerToggle）、`:18-22`（UPower D-Bus）；`js/ui/status/` 下 `network.js`、`volume.js`、`backlight.js` | 适配 | 顶栏是顶部横条、St 控件、依赖 Shell 的 Main；底部任务栏需换布局与容器，但“状态订阅（D-Bus 代理）+ 菜单聚合”模式可移植 | 改造：中（抽状态订阅逻辑，换 UI 壳） |
| 全屏切换（应用切换时全屏显示） | 适配（经合成器调用，不复制）：`mutter/src/core/window.c:8800`（meta_window_make_fullscreen）、`:8811`（unmake_fullscreen）、`:4041`（meta_window_activate） | 适配 | mutter 是合成器核心，复制单函数不可行；应经合成器接口调用 | 改造：小（切换逻辑调用现有接口） |
| 任务栏永远置顶 | 适配（经合成器调用，不复制）：`mutter/src/core/window.c:3717`（meta_window_make_above）、`:3735`（meta_window_set_above） | 适配 | 同上，不能复制函数；置顶需合成器层支持 | 改造：小 |
| 窗口模式 / 全屏模式切换选项（设置项） | 适配：`gnome-control-center/panels/power/cc-power-panel.c:372`（g_settings_set_boolean）、`:630,637`（g_settings_set_uint）的 GSettings 直写模式 | 适配 | 模式状态机本身新写；设置持久化模式可复用 GSettings 直写（见 §3） | 自写状态机：小；设置项：小 |
| 任务栏应用列表范围 | 待确认问题 Q5：原任务栏四项（AI / 设置 / Firefox / 终端）中 Firefox 已删；Chromium 是否占一个任务栏位，还是仅作普通应用（经应用发现列出）？本卡暂按“普通应用”计，确认后调整 | — | — | — |

## 2. 桌面 AI 看板（默认桌面）

用户定：桌面默认是一个看板，可看 AI 正在进行的操作（运行命令、修改的文件、tokens 消耗、模型等）。

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| DashboardShell（默认桌面视图：实时事件流视图） | 无等价实现。检索命令：`grep -rni "dashboard" gnome-shell/js/ui/`，零命中；openclaw 的会话视图面向网关 web 层，不作桌面壳 | 自写 | 参考库中无桌面看板实现 | 自写：大（实时事件总线 + 视图；数据源见下三行） |
| 事件源：命令执行记录 | 适配：`openclaw/src/gateway/operator-approval-store.rows.ts:20-28`（审批收据常量：保留期、摘要上限）；`openclaw/src/agents/agent-run-approval-wait.ts` | 适配 | 审批收据面向网关会话，需转为 OS 级事件流（命令、参数、结果、耗时） | 改造：中 |
| 事件源：文件修改记录 | 适配：`vscode/src/vs/workbench/common/editor/diffEditorModel.ts`（差异模型）；`openclaw/src/agents/tools/agent-step.ts`（单步执行入口） | 适配 | diff 模型是编辑器内模型，需接 agent 的文件写入事件并转为看板条目 | 改造：中 |
| 事件源：tokens / 模型统计 | 适配：`omniroute/src/lib/a2a/skills/smartRouting.ts`（路由决策输出：routing_explanation、cost_envelope） | 适配 | 路由解释面向单次调用，需聚合为会话级 tokens / 模型统计 | 改造：中 |
| 看板事件频率节流（性能约束） | 自写 | 自写 | 参考库无看板节流实现；用户定“尽可能轻量化高效化”，事件流必须节流/聚合，不得逐条刷 UI | 自写：小 |

## 3. 自建 GUI 设置软件（原生直接改配置，不走 API）

用户定：Omni 等 web 页面全部本地化，自建 GUI 设置软件、方便管理所有；
设置特别重写：系统内 GUI 原生直接改配置，比原来调用 API 改设置强。

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| SettingsApp 框架（面板导航、每页一面板） | 适配：`gnome-control-center/panels/`（CcPanel 子类模式，如 `panels/power`、`panels/system`） | 适配 | 面板框架组织模式可参考，但面板内容全部新写；不采用完整 gnome-control-center，只取组织模式 | 改造：中（框架移植 + 新面板） |
| 设置直写（GUI 直接改配置，不经 HTTP API） | 直接复用（调用模式）：`gnome-control-center/panels/power/cc-power-panel.c:372`（g_settings_set_boolean）、`:630,637`（g_settings_set_uint） | 直接复用（模式） | 该模式本身就是“GUI 直写 GSettings”，与用户“不走 API、原生直接改”完全一致；写目标从 GNOME schema 换成 DclawOS 自有 schema | 改造：小（换 schema 与键） |
| AI / 模型切换面板（替代 Omni web 仪表盘） | 适配：omniroute 引擎（见 §4）+ gnome-control-center 面板模式 | 适配 | Omni 的 UI 是 Next.js 网页（`omniroute/src/app/(dashboard)`），用户明确不要 web 管理；面板只调本地引擎，不走 HTTP | 改造：中 |
| DclawOS 自有 GSettings schema | 自写 | 自写 | 参考库中无 DclawOS schema；gsettings-desktop-schemas 不在本轮检索范围，是否补检索见待确认问题 Q6 | 自写：小 |
| 设置应用轻量化（性能约束） | — | — | 面板按需加载，不用 Electron / web 视图（与 §8 同理） | 约束，不单计 |

## 4. Omni 映像集成 + 特别重写

用户定：Omni 全部集成进映像，特别重写；不要 web 页面管理。

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| 路由引擎（智能路由、权重、分层回退、配额感知） | 适配：`omniroute/src/lib/combos/intelligentRouting.ts`、`controlCenter.ts`、`compositeTiers.ts`、`snapshotWeights.ts` | 适配 | TypeScript 服务端逻辑，需剥离为 OS 设置服务可调用的本地模块；剥离 Next.js / HTTP 包装；用户定“特别重写”+“全部集成进映像”，故不采用 npm 包运行时安装 | 改造：大（剥离 + 重写服务边界 + 映像内集成） |
| 供应商适配层 | 适配：`omniroute/src/lib/providers/`、`src/lib/providerModels/`、`src/lib/catalog/` | 适配 | 供应商接入最全，但需随引擎一并本地化；API key 等敏感配置走本地设置存储，不走 web | 改造：中 |
| 路由可解释性（routing_explanation、cost_envelope） | 适配：`omniroute/src/lib/a2a/skills/smartRouting.ts` | 适配 | A2A 包装层对设置软件多余，剥离；解释结构复用，供 §2 看板 tokens / 模型统计 | 改造：小 |
| Web 仪表盘 | 排除：`omniroute/src/app/(dashboard)` | 不采用 | 用户明确不要 web 管理；仪表盘不得复用 | —（删除，不计成本） |
| 映像集成形态（源码随映像编译 vs 预编译产物进映像） | 待确认问题 Q4：参考库无 Debian 打包方案；形态待用户在评估卡确认时定 | — | — | 打包：中（形态定后） |
| 品牌 | — | — | MIT 不覆盖商标；OmniRoute 产品名、图标须改名换标 | 约束，不单计 |

## 5. 特权权限代理层（内核级特权）

用户定：内核级别特权，按计划做（即特权代理层，不是字面定制内核模块）；
AI 执行操作前须用户确认。

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| 审批中枢（工具调用前审批：请求 / 等待 / 延期 / 超时） | 适配：`openclaw/src/agents/agent-tools.before-tool-call.approval.ts`；`agent-tools.before-tool-call.policy.ts:61`（getBeforeToolCallPolicyDiagnosticState）、`:69`（hasBeforeToolCallPolicy）、`:76`（runBeforeToolCallHook） | 适配 | agent 工具层审批，不是 OS 层拦截；需在其下加特权代理层做强制（无审批票据则拒绝执行） | 改造：大（审批语义复用 + 强制层新写，见下行） |
| 审批存储与收据（持久化、审计链） | 适配：`openclaw/src/gateway/operator-approval-store.rows.ts:20-28`（收据常量）；`operator-approval-authorization.ts`；`agent-runtime-approval-authority.ts`；`src/infra/` 下 `agent-run-approval-leases.ts`、`approval-channel-policy-support.ts`、`approval-display-paths.ts` | 适配 | 面向网关会话，需适配为 OS 级“操作票据”模型（票据与执行强制挂钩，见下行） | 改造：中 |
| 特权代理层（OS 强制执行：无票据拒绝） | 自写 | 自写 | 参考库中无 OS 层强制拦截实现；openclaw 审批是协作式（工具主动调用钩子），绕过钩子的工具不受限，故强制层必须新写（特权服务 + 执行网关） | 自写：大 |
| 危险操作分级（策略规则） | 适配（入口）+ 自写（规则）：策略入口 `runBeforeToolCallHook`（`policy.ts:76`）可复用思想 | 适配入口 / 自写规则 | 分级规则按 OS 操作重写，参考库无 OS 操作分级表 | 自写规则：中 |

注：`RESEARCH.md` 提及的 `src/infra/approval-scope`、`plugin-approvals`、`exec-approval-surface`、`standing-grants`、
`exec-approval-manager` 本轮未逐项定位，以上以本轮实测文件为准；确认前可补核。

## 6. AI 操作 GUI + 主动请求人类接管

用户定：AI 请人接管特指 AI 主动请人类帮助过人机验证、登录账号等。

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| GUI 操作（截图 + 坐标：点击 / 拖拽 / 按键） | 适配：`openclaw/src/agents/tools/computer-tool.ts:61-86`（动作表：left_click、left_click_drag 等）、`:583`（screenshot / wait / take_control 分支） | 适配 | 依赖运行中桌面会话与网关；需改为经 OS 合成器 / 输入层执行（不依赖网关会话），且受 §5 特权代理约束 | 改造：大 |
| take_control | 不采用（方向相反）：`openclaw/src/agents/tools/computer-tool.ts:102`（把人类查看者切为仅查看并返回新截图） | 不采用 | 该语义是 AI 从人手里接管，与用户要的“AI 请人代操作”方向相反 | —（反向设计见下行） |
| 主动请求人类接管（人机验证、登录账号等） | 适配（改造为接管流程）：`openclaw/src/agents/tools/ask-user-tool.ts:40`（ASK_USER_PROMPT_RECHECK_MS）、`:323`（等待循环）、`:460`（describeAskUserTool） | 适配 | 现状只是问答等待，不是接管流程；需改造：AI 发起接管请求 → 人类在人类会话中操作（验证 / 登录）→ 结果回填 AI 会话 → 超时与取消语义新定 | 改造：大 |
| 接管结果回填与超时 / 取消 | 自写 | 自写 | 参考库无回填语义（问答只回答案文本）；回填内容、超时、取消需新定义。安全约束：回填只回“成功 / 失败 + 会话句柄”，不得回填登录凭证明文（见待确认问题 Q2） | 自写：中 |
| agent 执行循环单步 | 适配：`openclaw/src/agents/tools/agent-step.ts` | 适配 | 循环骨架可参考；OS 级循环需另定任务 / 会话 / 取消 / 恢复语义 | 改造：中 |

## 7. AI / 人环境分离 + 随时监视

用户定：AI 操作环境和人尽量完全分开，人类可以随时监视 AI 工作。

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| 会话 / 环境分离（AI 在独立环境运行） | 部分适配（思想）：`openclaw/src/agents/agent-scope-config.ts:285-321`（agent-scoped session key）；`src/agents/mcp-app-sandbox.ts`、`core-coding-tools.sandbox.test.ts`（agent 级沙箱） | 适配（思想） | 均为 agent 级沙箱 / 作用域，不是 OS 级会话分离；OS 级需另做：独立用户 / 会话、独立显示与输入面、AI 会话无人类会话输入权限 | 改造：大 |
| 人类监视面（只读看板） | 自写（与 §2 看板同一实现，不重复计） | 自写 | 无等价实现（见 §2 检索） | 与 §2 合并计 |
| 监视与执行的权限边界 | 自写 | 自写 | 参考库无 OS 级监视 / 执行权限模型；需新定义（见待确认问题 Q1：人类监视时能否干预 / 叫停） | 自写：中 |

## 8. 编辑器（快速编辑代码、查看被 agent 修改的文件）

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| Monaco 编辑器核心 | 直接复用（组件）：`vscode/src/vs/editor/editor.main.ts`、`editor.api.ts` | 直接复用 | 可嵌入的独立版 Monaco；不采用 Electron 工作台（太重，违背轻量化） | 集成：中（构建与打包 Monaco，而非复制整个工作台） |
| diff / multi-diff（查看被 agent 修改的文件） | 直接复用（组件）：`vscode/src/vs/editor/browser/widget/diffEditor`、`multiDiffEditor`；`src/vs/workbench/common/editor/diffEditorModel.ts` | 直接复用 | 需接 agent 文件变更记录（§2 事件源） | 集成：中 |
| 文件浏览 / 源码管理 / 终端面板 | 仅结构参照：`vscode/src/vs/workbench/contrib/{files,scm,terminal}` | 不直接复用 | 全量工作台不采用；轻量编辑器按需裁剪；编辑器内是否内嵌终端见待确认问题 Q7（系统终端应用已存在） | 参照：小 |
| 品牌 | — | — | MIT 不覆盖商标；VSCode 产品名、图标不得照搬，改名换标 | 约束，不单计 |

## 9. MCP + skills 完整支持

用户定：支持完整 MCP、skills 等。

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| MCP 客户端 / 目录 / 获取 | 适配：`openclaw/src/agents/agent-bundle-mcp-{catalog-projection,combined,harness}.ts`、`mcp-app-sandbox.ts` | 适配 | agent bundle 式 MCP 集成，需转为 OS 级 agent 服务的 MCP 层；“完整 MCP”指客户端全协议 + 审批（见下行） | 改造：大 |
| MCP 工具调用审批 | 适配（待复核）：`openclaw/src/gateway/operator-approval-mcp-grants.test.ts`（测试文件已见；实现文件本轮在网关目录未定位，确认前复核） | 适配 | 审批语义复用 §5 中枢；实现文件位置待复核后定 | 改造：中 |
| skills 加载 / 发现 / 生命周期 | 适配：`openclaw/src/skills/{config,discovery,library,lifecycle,loading,runtime,security}` | 适配 | skills 子系统完整，需随 agent 服务一并本地化；第三方 skill 包的许可证逐项核对 | 改造：中 |
| omniroute agentSkills 与 openclaw skills 的关系 | 待确认问题 Q3：`omniroute/src/lib/agentSkills` 与 `openclaw/src/skills` 两套体系是否统一、以哪套为准 | — | — | 改造：中（统一后） |

## 10. Chromium 打包

| 目标符号 | 参考库候选（path:line） | 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计 |
|---|---|---|---|---|
| Chromium 浏览器本体 | 打包复用：Debian 源 `chromium` 包 | 打包复用 | 不从源码复制（数 GB 源码 + 数小时构建）；AI 操作网页走 §6 computer-tool 层，与浏览器本体分开 | 打包：小 |
| 浏览器在任务栏 / 启动器中的入口 | 自写（普通应用入口，经应用发现列出） | 自写 | 任务栏位问题见 Q5；应用发现用 `gnome-shell/js/ui/appDisplay.js:1515`（`Shell.AppSystem.get_default().get_installed()`）模式 | 自写：小（与 §1 合并） |

## 11. 构建 / 打包说明

- 构建方式：用户已定 GitHub Action 构建；构建配置在评估卡确认后写（沿用旧 DclawOS 的 live-build 模式）。
- 映像内容清单：Omni 集成（§4）、Chromium 包、Monaco、agent 服务、设置应用、看板、任务栏。
- 应用发现（所有应用）与窗口调度：GNOME 范围用户已定为仅此两项，复用点为
  `gnome-shell/js/ui/appDisplay.js:1372`（installed-changed 订阅）、`:1515`（get_installed）、
  `altTab.js:74`（get_running）、`mutter/src/core/window.c:3955`（activate_full）、`:4041`（activate）、`:5132`（focus）、`:2973`（maximize）。
  不采用完整 gnome-shell / mutter 之外的 GNOME 组件。
- 性能横切约束（用户定）：每个模块实现时附性能影响说明（Monaco 构建体积、TS 服务运行时开销、看板事件频率节流）；
  不得引入 Electron / web 视图等重型运行时。

## 待确认问题（卡住具体行，评估卡确认时一并定）

1. Q1：人类监视时能否干预 / 叫停 AI 会话？还是只能看？——卡住 §7 权限边界行。
2. Q2：接管结果回填是否只回“成功 / 失败 + 会话句柄”，绝不回填登录凭证明文？（本卡按此安全默认值写，如用户另有要求请改。）——卡住 §6 回填行。
3. Q3：openclaw skills 与 omniroute agentSkills 两套体系是否统一、以哪套为准？——卡住 §9。
4. Q4：Omni 映像集成形态：源码随映像编译，还是预编译产物进映像？——卡住 §4 打包行。
5. Q5：任务栏应用列表范围：Chromium 是否占一个任务栏位，还是仅作普通应用？——卡住 §1、§10。
6. Q6：GSettings 底层是否补检索 gsettings-desktop-schemas / gnome-settings-daemon？（GNOME 范围已定为应用发现 + 窗口调度，但设置 schema 的底层依赖未定。）——卡住 §3 schema 行。
7. Q7：轻量编辑器内是否内嵌终端，还是只用系统终端应用？——卡住 §8 参照行。

## 草案说明

本文件为评估卡草案，待用户确认。用户确认后，写代码时按第 3 步执行：
自写代码先移植参考项目的测试 / 示例并通过、注释写 `// reuse-check: <path:line> 未采用，原因：<理由>`、
行为边界错误处理与参考实现对齐；每完成一个文件汇报改动、引用位置、测试结果。
本文件不构成产品代码，不改动任何仓库；正式发布前以各仓库根 LICENSE 与各文件头为准复核许可证一次。
