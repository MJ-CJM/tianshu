# ADR-0010:锦衣卫——运行时深防御四件套的边界与默认值

- 状态:已接受
- 日期:2026-07-08(迭代 3「深防御」)
- 相关:[ADR-0003](./adr-0003-trust-defaults.md)(信任默认值)、DECISIONS.md D15/D16

## 背景

治理是天枢最强卖点(护城河=治理×自进化)。迭代 0-2 的治理停在**决策层**
(tier/策略管线/批红/审计);迭代 3 把它做到**运行时层**——一次工具调用真正
执行前后的最后一公里。参考项目里 multica 与 zeroclaw **各自独立**实现了出站
脱敏,这种"两个团队不约而同做同一件事"是"必须做"的强信号。

## 决策

统称**锦衣卫**(运行时安全监察),四件套 + 三条边界声明:

### 1. 出站脱敏(redact)挂在出站面,不挂执行语义面
- **挂**:WS 广播 / webhook / 通知渠道外发——不可撤回、面向人的最后一公里。
- **不挂**:`memorial.result`(含规划/中间步骤的审计全量,follow-up 要用它重建
  history,脱敏会破坏语义);events 落库 payload 由调用方选择性 redact。
- 已知局限:流式 delta 若把一个 secret 切进两个 chunk,单片匹配不到——红队
  用例标注,不假装完美。

### 2. bash 风险分级按分段判定,"最高危胜出"
- 旧的 `command.startswith(prefix)` 白名单有真实洞:`git log; rm -rf /` 以白名单
  前缀 `git ` 开头会被放行。改为 quote-aware 分段后**逐段**判定:黑名单任一段
  命中即 deny;白名单须**每段**命中才 allow;命令替换/重定向/后台 `&` 等结构
  绕过一律升级审批(白名单看不见隐藏子命令)。
- 不追求完整 shell AST(tree-sitter 依赖重):quote-aware 分段 + 结构检测已覆盖
  绝大多数绕过,失守时向"审批"而非"放行"降级(fail-secure)。

### 3. clean-env 白名单,secret 不经环境变量泄漏
- 天枢进程 env 里有全部 secrets;`shell_exec` 继承全量 env 则一句
  `echo $TIANSHU_LLM_API_KEY` 即泄漏。默认只透传终端/locale/路径类白名单,
  业务额外变量经 `TIANSHU_SHELL_ENV_PASSTHROUGH` 显式声明。
- MCP stdio 子进程无需额外处理:官方 SDK 在 `env=None` 时用其
  `get_default_environment()` 白名单,天枢只在用户**显式**配置 server env 时透传
  (显式即有意为之)。

### 4. 分级急停在工具管线**最前**,先于一切判定
- 三档(全停/掐网/冻结工具)可叠加,SQLite 单行状态持久化,读取损坏
  **fail-closed**(视为全停)。放在 `ToolRegistry.execute` 入口最前——kill_all
  下连 T0 只读也拒(急停就是要立即、无差别生效)。
- 急停自身**不设审批门**:审批会延迟生效,与"急刹车"语义矛盾;但全部
  engage/resume 留事件账本(producer="estop")。

## 默认值(延续 ADR-0003 的信任默认值哲学)

| 能力 | 出厂默认 | 理由 |
|---|---|---|
| 出站脱敏 | **开** | 泄漏不可撤回,默认必须护 |
| bash 分段分级 | **开** | 纯本地判定,零成本 |
| clean-env | **开** | 同上 |
| 分级急停 | 待命(未 engage) | 机制常驻,由人触发 |
| 出厂预算护栏 | **开**,¥20/日 | 放手四保险,防失控烧钱 |
| opt-in 遥测 | **关** | 隐私默认关,一行 env 永久关 |
| OTel 埋点 | **关** | 本地默认关,设 endpoint 才导出 |
| MCP 准入清单 | 不强制(明示告警) | 强制会破坏既有部署;生产建议显式配 |

## 诚实声明的边界(D15)

- 天枢的 MCP **server**(`POST /mcp`)只暴露手选的 5 个 tools,**不做流量代理**、
  不转发任意外部请求。
- 天枢作为 MCP **client** 连接外部 server 时,受准入清单 + tier 治理;但天枢
  **不为外部 server 的行为背书**——准入清单是"你信任谁",不是"天枢保证谁安全"。
- 脱敏/分级/急停降低泄漏与失控面,**不等于**沙箱隔离;真正的强隔离在迭代 3.5
  影子快照 + 迭代 6 变体容器化(禁网/限额)。

## 影响

- 新增 `tianshu.security` 包(redact/clean_env/bash_analysis/estop),纳入 mypy 覆盖。
- `ToolRegistry` 加急停入口检查(热路径,estop 未注入时零成本)。
- bash_safety 策略规则行为变更:此前被 startswith 误放行的复合命令现在按段判定,
  可能把一些原先 allow 的复合命令降级为 require_approval——这是修复,不是回归。
