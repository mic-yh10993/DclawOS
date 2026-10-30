# DclawOS 特权模型（Phase 3 模块 4 修订版）

本文档记录 2026-10-30 用户纠正后的特权模型，取代 Phase 2 的
“AI 用户无 sudo”表述。Phase 2 的分离机制（用户、组、目录权限）
不变；变化的是提权通道与授权规则。

## 角色

- `dclaw`：人类用户。live 环境下 NOPASSWD sudo（见模块 5 的
  live/install 区分）；安装后系统按正常用户权限管理。
- `dclaw-agent`：agent 服务的运行用户。**不在任何 sudoers 文件里**
  （校验脚本强制检查）；它没有任何直接提权路径。
- `dclawos-privileged`：root 运行的特权 broker。唯一提权通道：
  Unix socket（组 dclawos-session 可写），每次执行必须持有已批准
  且未过期的票据，且票据的操作与参数哈希必须与请求完全一致。

## 授权分层（agentlib.classify）

- tier 0（免票据）：只读操作——路由决策、服务状态、工作区内
  列目录/读文件、/var/lib/dclawos 与 /etc/dclawos 内读文件、
  接管请求本身（人的操作在带外完成）。
- tier 1（需票据批准）：工作区内写文件、shell、服务重启、
  软件安装（broker 的 package-install）、MCP 调用、Skill 运行、
  子代理、连接器发送。
- tier 2（需人工明确批准）：删文件、工作区外读写、未知工具
  （未知工具直接失败，不进入执行）。

## 完全访问开关（agent-full-access）

- GSettings 键 `org.dclawos.settings agent-full-access`，默认 false，
  由设置软件「通用」页的开关写入；agent 服务只读不写。
- 开启后：所有票据仍被创建并记入事件日志（审计链完整），但
  状态直接置为 approved，不再弹窗等待人工批准。
- 关闭后：tier 1/2 每次调用都必须经人工批准（审批弹窗）或过期。

## 无沙箱

- DclawOS 自身代码没有任何沙箱（bubblewrap、命名空间隔离等），
  这是用户明确要求（沙箱影响性能）。agent 进程以 dclaw-agent
  用户直接运行；隔离靠用户/组/目录权限 + 票据 broker 实现，
  不靠沙箱。
- 引用：vendored 的上游引擎源码里出现 `sandbox` 字样（如技能
  沙箱特性开关）时，那只是上游特性常量，不接线到 DclawOS 的
  执行路径；路由决策不执行技能，不产生沙箱行为。

## 审计

- 每次票据创建、批准/拒绝、自动批准（完全访问）、broker 执行，
  都记入事件流；票据摘要与日志永不含密钥明文（票据只存参数
  哈希；密钥只经 broker write-secret 写入 0600 文件）。
- 密钥存储用 broker 写 0600 文件，不用 libsecret：dclaw-agent
  是无登录会话的系统用户，没有可解锁的 keyring，root 也无法
  解开人类用户的 keyring；文件存储是服务用户下的等价方案。
