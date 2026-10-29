# DclawOS

Debian 系、内核级 AI 操作系统（从零重做）。

> 当前状态：基础骨架阶段。计划待用户审批，尚无产品代码。
> 在计划获批、复用评估卡获用户确认之前，本仓库不接受任何产品代码提交。

## 愿景

- AI 全栈环境，但不直接跑完整 GNOME 与 OpenClaw：借鉴其源代码，深度融入操作系统。
- 系统几乎完全 AI 化：AI 至上，人类只是辅助 AI。
- AI 可以操作底层系统与一切软件（含 GUI 软件），但执行操作必须经过用户确认（权限审查）。
- AI 操作网页等任务时，可以请求真人接管代操作。
- 不要臃肿：不要完整 GNOME 桌面、不要 Office、不要图片视频查看器；只要轻量窗口切换 / 应用启动器。

## 界面草图

- 屏幕下方：类似任务栏的东西，切换系统应用：AI / 设置 / Firefox / 终端。
- 屏幕右上角：电源键、状态栏、监控。
- 浏览器：Chromium（Debian 源包，打包复用）。
- 设置软件：内含 AI / 模型动态切换面板（不要 web 页面管理）。

## 架构草图（计划审批前为草案，以计划邮件为准）

| 组件 | 做法 | 参考 |
|---|---|---|
| 合成器 / 窗口调度 | 使用 mutter 作为合成器（系统包），不复制其 C 代码；轻量壳参考其窗口激活与帧调度逻辑 | mutter `src/core/window.c`、`src/compositor/` |
| 窗口切换 / 启动器 | 新写轻量壳，参考 GNOME Shell 的切换器与启动器逻辑，脱离 Shell 重写 | gnome-shell `js/ui/altTab.js`、`js/ui/appDisplay.js` |
| 底部任务栏 | 新设计（GNOME 本身无底部任务栏，只有顶栏模式可参考） | gnome-shell `js/ui/panel.js`（顶栏三栏与状态区聚合模式） |
| 权限审查 / 用户确认 | 审批中枢参考 OpenClaw 的工具调用前审批；OS 层强制需另加特权代理层 | openclaw `src/agents/agent-tools.before-tool-call.*`、`src/gateway/operator-approval-store.*` |
| AI 操作 GUI / 真人接管 | 参照 computer-tool 的截图坐标操作与 ask-user-tool 的问答等待，改造成接管流程（方向与 take_control 相反） | openclaw `src/agents/tools/computer-tool.ts`、`ask-user-tool.ts` |
| 动态模型切换（进设置） | 复用 OmniRoute 的 combos / providers 引擎，剥离其 Next.js 网页仪表盘；设置面板按 CcPanel 模式新写 | omniroute `src/lib/combos/`、`src/lib/providers/`；gnome-control-center `panels/` |
| 编辑器 | 以 Monaco 组件复用，不采用整个 VSCode 工作台（Electron 太重）；查看被 agent 修改的文件用 diff / multi-diff 组件 | vscode `src/vs/editor/editor.main.ts`、`src/vs/editor/browser/widget/diffEditor` |
| 浏览器 | Debian 源 `chromium` 包，不从源码复制 | — |

## 复用优先流程（工作合同，不可跳步）

详见 [docs/REUSE-PROCESS.md](docs/REUSE-PROCESS.md)。摘要：

- 第 0 步：检索。写任何代码前，必须对参考库执行检索并贴出命令与结果，输出“候选实现清单”（文件:行号 + 做了什么 + 与需求的差距）。
- 第 1 步：复用评估卡。每个新增/修改的函数、类、模块填写：目标符号 | 参考库候选（path:line）| 能否直接复用/适配 | 不能用的具体理由 | 自写 vs 改造成本估计。只有具体理由成立（依赖冲突、API 不兼容、许可证、版本差异、缺少关键分支）才允许自写；“自己写更简单”“风格不同”“想统一架构”不是合法理由。
- 第 2 步：等用户确认评估卡后，才允许写代码。
- 第 3 步：自写代码必须先移植参考项目的测试/示例并通过；注释写 `// reuse-check: <path:line> 未采用，原因：<理由>`；行为、边界条件、错误处理与参考实现对齐，除非评估卡明确说明差异。
- 第 4 步：每完成一个文件，贴出：改了什么、引用了哪些参考位置、测试结果。
- 禁止：未检索就新增符号；参考库有等价实现却不说理由；把“参考”当“灵感”。

## 参考仓库与开源边界

| 参考仓库 | 许可证 | 使用方式 |
|---|---|---|
| openclaw/openclaw | MIT | 复制时保留版权声明；商标另算 |
| diegosouzapw/OmniRoute | MIT | 同上 |
| microsoft/vscode | MIT | 同上；产品名、图标、品牌不得照搬，须改名换标 |
| GNOME/gnome-shell、mutter、gnome-control-center | GPL-2.0-or-later | Copyleft：分发衍生二进制须按 GPL 提供源码，不能链接进闭源组件 |

本仓库当前许可证为 GPL-2.0-or-later（与将要复用的 GNOME 代码一致）。
最终定向（GPL 整仓 vs MIT + GPL 部件独立成包）待计划审批时由用户定；
在定向确认前，本仓库不并入任何 GPL 代码文件。
各参考仓库的捆绑依赖各有自己的许可证，评估卡阶段须按文件头逐项核对。
本说明不构成法律判断；正式发布前以各仓库根 LICENSE 与各文件头为准复核一次。

## 构建

GitHub Action 构建（与旧 DclawOS 相同模式），构建配置随计划审批后的实现一并落地。
旧 DclawOS 仓库已按用户指令删除（本地有完整镜像备份，不公开保留）。
