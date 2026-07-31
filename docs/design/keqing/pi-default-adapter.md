# 客卿体系设计：pi 为默认执行内核（历史/演进方案）

> **状态说明（当前实现优先）**：本文是目标架构和阶段方案，不是已完成能力清单。当前
> `/keqing` 为默认导航隐藏的实验路由，生产 executor 只支持 CLI 自管凭证；凭证网关未接入，
> 尝试开启返回 `409`。本文中的 scoped token、hard cost cap、raw-key 隔离、provider
> headers 和完整 guard/session 闭环，除非在
> [`management-page.md`](management-page.md) 或
> [`../../CURRENT-STATE.md`](../../CURRENT-STATE.md) 明确列为当前可用，否则均不得作为发布
> 承诺。

> 2026-07-23。前置文档：[grok-build/pi 借鉴分析与客卿抽象层设计](../../plan/2026-07-23-grok-pi-borrow-and-agent-adapter.md)（adapter 抽象、能力协商、候选矩阵）。
>
> 本文是该抽象层的**默认实现方案**：以 pi（[pi-mono](https://github.com/earendil-works/pi)，MIT，TypeScript）为默认客卿执行内核，Claude Code/Codex/opencode 作为备选方言。

---

## 一、决策与理由

**决策**：天枢客卿（外部 coding agent 执行自进化开发任务）的默认 adapter 为 **PiAdapter**；Claude Code 降为备选（能力对照、用户订阅成本优势场景）；Codex 保留 partial 降级验证；opencode/hermes 按需接入。

理由（按权重）：

1. **不锁定**：pi 是 MIT 开源 + 统一多 provider 层（Anthropic/OpenAI/Google/OpenRouter/本地 llama.cpp 等），模型选择是天枢策略的一部分而非供应商既定事实。Claude Code 绑定 Anthropic 订阅/API，作为默认内核会把天枢的自进化能力绑在单一供应商上。
2. **协议质量**：pi 的 RPC 模式是六个候选里最干净的机器驱动协议（LF-only JSONL、id 配对命令流 + 无 id 事件流、`agent_settled` 三层结算、会话树 fork/switch、扩展 UI 反向通道）。
3. **治理拦截深度**：pi 扩展 API 允许天枢把治理逻辑装进 agent 进程内部（`tool_call` block / `before_provider_headers` / operations 换执行后端），拦截深度超过 Claude Code 的 command hook。
4. **哲学契合**：pi 刻意不做权限系统与沙箱，明确把隔离责任交给宿主——天枢正是那个宿主。它缺的层恰好是天枢的护城河层，组合无重叠、无摩擦。
5. **可审计可修**：~12 万行 TS 全量可读（本次已深读），出问题可以钉版本、可以 fork、可以提 patch；闭源 CLI 做不到。

代价与对策见 §八。

---

## 二、总体架构

```
┌─────────────────────────────────────────────────────────────┐
│ 天枢治理核心（协议无关，不变）                                   │
│  edict(需求+验收物) · PolicyEngine · 裁决/廷议 · Auditor        │
│  起居注 · memorial · 户部 · 配对沙箱评估                        │
├─────────────────────────────────────────────────────────────┤
│ KeqingSupervisor（进程托管）                                   │
│  spawn/状态机/意外退出兜底/重启对账（参照 pi packages/server）     │
├──────────────┬──────────────┬──────────────┬────────────────┤
│ PiAdapter    │ ClaudeCode   │ Codex        │ (opencode/     │
│ 【默认】      │ Adapter      │ Adapter      │  hermes 后续)   │
│ rpc + guard  │ stream-json  │ exec         │                │
│              │ + hooks shim │ (partial)    │                │
├──────────────┴──────────────┴──────────────┴────────────────┤
│ 硬保证层（对所有 adapter 无条件生效）                            │
│  worktree 隔离 · clean-env · 凭证网关(scoped token) ·          │
│  预算熔断 · 事后验收 · 三方合并发布                              │
└─────────────────────────────────────────────────────────────┘
        │ 凭证网关（LLM 出口唯一通道）
        ▼
   Anthropic / OpenAI / Google / OpenRouter / 本地模型 …
```

pi 路径的进程视图：

```
天枢 (Python/FastAPI)
  └─ KeqingSupervisor.spawn
       └─ 子进程: pi --mode rpc --no-session -e <tianshu-guard.ts> \
                    --session-dir <workspace>/.tianshu/sessions
            stdin  ◄── JSONL 命令（prompt/steer/abort/switch_session/…）
            stdout ──► JSONL 响应(id 配对) + 事件流 + guard 上报
            环境   ：clean-env + PI_GATEWAY_TOKEN(scoped) + guard 配置路径
            cwd    ：隔离 worktree
```

---

## 三、PiAdapter 设计

### 3.1 进程与生命周期

- **spawn**：`pi --mode rpc -e <guard> --session-dir <ws>/.tianshu/sessions`，cwd 指向隔离 worktree，clean-env 白名单注入（仅 PATH/HOME 必需项 + 网关 token + guard 配置路径）。
- **关闭**：正常路径关 stdin（EOF → pi 优雅 shutdown，`agent_settled` 后退出）；超时 SIGTERM，宽限后 SIGKILL（进程组）。
- **状态机**（借 pi server supervisor）：`starting → online → working → settling → stopped/error`；意外退出 → 状态置 error + stderr 缓冲入起居注 + 挂 memorial。
- **重启对账**：supervisor 台账（attempt lease 已有）重启后将 online 记录降级 stopped；**未完成的下发绝不自动重发**（pi durable 恢复纪律：非幂等副作用缺终态才补做，重发须经人工或验收判定）。

### 3.2 协议消费（wire 契约）

- 分帧：LF-only JSONL（Python `asyncio.StreamReader.readline` 天然合规）；写入 `json.dumps(cmd) + "\n"`。
- 命令：带 `id`（天枢生成 `tianshu-<seq>-<uuid>`），response 按 id 配对，超时按命令类型分级（prompt 无超时靠事件流，管理类 10s）。
- **结算锚点**：编排器只认 `agent_settled` 推进（`agent_end.willRetry=true` 时继续等待）；`is_settled` 前不做验收、不回收工作区。
- 事件消费分层：
  - 必消费：`agent_start/end/settled`、`turn_end`、`tool_execution_start/end`、`message_end`（usage 累计）、`extension_ui_request`（guard 请示通道）
  - 转发起居注：以上全部 + `compaction_*`、`auto_retry_*`、`queue_update`（归一为 CanonicalAgentEvent，B2 envelope）
  - 可选消费（实时面板需要时）：`message_update` token 级 delta
- **会话树利用**：`get_entries{since}` 增量拉取入起居注（断线重连游标）；`fork(entryId)` 支撑「从某决策点重开一路」的多方案探索。

### 3.3 能力声明

```python
PI_CAPABILITIES = AgentCapabilities(
    permission_shaping="full",      # via tianshu-guard（进程内，深于 CLI 参数）
    hooks="in_process",             # guard 扩展；新增枚举值，深于 command_shim
    stop_gate=True,                 # 等价实现：settled 后验收 + follow_up 回灌（免进程重启）
    session_resume=True,            # switch_session / fork / entries 树
    interject=True,                 # steer（turn 间插话）/ follow_up（停后追加）
    usage_reporting="full",         # message usage + get_session_stats
)
```

---

## 四、tianshu-guard：进程内治理扩展（本方案核心构件）

一个由天枢维护、随 adapter 版本钉死的 pi 扩展（TypeScript，单文件优先），经 `-e` 显式注入。四层职责映射 pi 的治理挂点：

### 4.1 准入层（会中工具拦截，A2 的 pi 实现）

- `pi.on("tool_call")`：按 guard 配置（天枢 PolicyCompiler 产物，JSON，spawn 前写入 workspace 外的受控路径）本地快速裁决：
  - deny 命中 → `{block: true, reason}`（reason 进起居注）
  - bash 命令走段级不对称匹配（E2 语义：deny 逐段+全串、allow 仅全串、不可拆升 ask）
  - **ask 档 → 经 `extension_ui_request` 反向通道上报天枢**（confirm/select 语义），天枢裁决 UI 处理后回 `extension_ui_response`；**timeout 兜底 = 拒绝**（修正 pi 默认放行的软肋）
- 本地裁决优先、上报仅 ask 档：会中拦截不产生每工具一次的网络往返。

### 4.2 传输层（凭证隔离 + 模型治理）

- `pi.registerProvider` 覆盖：把 policy 允许的每个 provider 的 `baseUrl` 重定向到**天枢凭证网关**，`apiKey` 用环境变量注入的 **per-run scoped token**（`$TIANSHU_GATEWAY_TOKEN`）。raw key 永不进入客卿进程/工作区（OpenShell 模式）。
- policy 不允许的 provider → `unregisterProvider` 摘除；模型白名单经 guard 配置约束 `set_model` 请求（`model_select` 事件校验 + 越权即 abort 上报）。
- `before_provider_headers`：注入 `X-Tianshu-Edict-Id` / `X-Tianshu-Run-Id` 归因头（网关侧计费与审计按 run 维度记账）。

### 4.3 会话与来源防护

- `pi.on("project_trust")`：按天枢 policy 返回 `{trusted: "no"}`（默认）——**被开发仓库里的 `.pi/` 扩展/技能一律不加载**，防 repo 控制的代码在客卿进程内自我授权（pi 防提权门的治理化使用）。
- `session_before_switch/fork` 默认放行（天枢自己驱动的操作），但校验目标路径在受控 session-dir 内，越界 cancel。

### 4.4 观测层

- `agent_start/end` 成对埋点（per-turn token/耗时/成本，tps.ts 范式）经事件流附带上报。
- guard 自身失败策略：**fail-closed**——guard 加载失败/配置缺失时，由 adapter 在 spawn 后自检（发一条 guard 握手命令，guard 用 `registerCommand` 应答），握手失败即终止本次运行并挂 memorial。这与 pi 原生 hook 的 fail-open 相反，是天枢的治理底线选择。

### 4.5 防篡改边界（诚实声明）

guard 运行在客卿进程内，理论上可被同进程内代码干扰（pi 扩展无沙箱）。因此 guard 属**软增强层**；纵深防御依赖硬保证层：

- 凭证与预算强制点在**网关**（scoped token 只能访问白名单模型、预算耗尽网关直接断供——hard cost cap 由此落地，不再依赖事后杀进程）；
- 文件系统边界在 worktree + 后续 OS 级沙箱；
- 最终关卡在验收 + 三方合并（产出不经裁决不落主仓）。

---

## 五、凭证网关与多模型治理（pi 默认化后的升级项）

选 pi 的核心收益（多模型）由这一层兑现：

1. **scoped token**：每次 spawn 签发 per-run 令牌（绑定 edict_id/run_id/模型白名单/预算额度/TTL），网关映射到真实 provider key。可即时吊销；泄漏面 = 单次运行。
2. **hard cost cap**：网关按 token 累计计费，超额直接 402 断供（客卿收到的是可分类的确定性错误 → 走「额度耗尽不重试」分类）。天枢测绘 gap「预算熔断靠事后杀进程」由此根治。
3. **模型路由策略**：edict 元数据（任务难度/成本档位）→ policy 决定模型白名单与默认模型；简单脚手架用廉价模型、难题用旗舰模型；失败升级路径（cheap → strong）由天枢编排层触发（follow_up 回灌时 `set_model`）。
4. **多模型评估轴**（新能力）：同一 edict、同一 harness（pi）、不同模型 → 配对沙箱评估分离「harness 质量」与「模型质量」两个变量。位面竞争力评估从此可以回答「换模型值不值」，户部有逐 run 成本数据支撑 ROI。
5. 网关实现优先复用现有 LLM 代理设施（litellm proxy 或轻量 FastAPI 反代），E1 重试分类/Retry-After 逻辑同层落地。

---

## 六、验收闭环（pi 版，优于 Claude Code 版）

```
spawn(pi rpc + guard) → prompt(edict 需求规格)
   → 事件流 → 起居注/计费/拦截
   → agent_settled
   → 天枢跑验收物（测试/脚本/Auditor 规则 + memorial 验收契约）
        ├─ 不合格 → follow_up(整改意见) → 同会话继续（上下文完整保留，
        │            免进程重启、免 -r 重载）→ 循环，上限 N 次（默认 3）
        │            连续否决入起居注；N 次仍不合格 → 挂 memorial
        └─ 合格 → get_session_stats 终账 → 关 stdin 优雅收尾
                → WorkspaceApplyEngine 三方合并（冲突进入裁决）
                → 配对评估/廷议终审 → 晋升
```

相对 Claude Code 版的两点优势：

- **回灌免重启**：`follow_up` 在同会话追加，上下文/工作记忆完整；Claude Code 需 `-r` 重启进程重载会话。
- **多方案探索**：验收发现方向性问题时可 `fork(entryId)` 回到决策点开新分支，而非从头重跑——廷议式分支进入 coding 会话内部。

---

## 七、备选 adapter 定位

| Adapter | 定位 | 触发场景 |
|---|---|---|
| ClaudeCodeAdapter | 第一备选 + 对照基线 | 用户 Claude 订阅成本更优时；多客卿配对评估的对照组；pi 故障降级 |
| CodexAdapter | partial 降级验证 | 验证抽象层降级语义；轻量任务 |
| opencode / hermes | 按需接入 | 多客卿竞争评估扩容时（hermes 须关自主学习） |

抽象层不因 pi 默认化而弱化：**PolicyCompiler 对 pi 的编译目标是 guard 配置 JSON**（对 Claude Code 是 settings permissions + CLI 参数）——同一 policy 引擎、不同方言产物，这正是 adapter 契约的设计验证。

---

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| pi 迭代快，RPC/扩展 API 可能破坏性变更 | **钉版本**（锁定 npm 版本或 vendored 二进制）；wire 类型 vendored 进天枢（Pydantic 模型）；**契约测试套件**（借 pi 自己给 provider 的测试套路：对钉死版本跑 spawn/prompt/block/settled/resume 全链路），升级 = 重跑契约套件绿了才动 |
| guard 可被进程内代码绕过 | §4.5 纵深防御：网关（凭证/预算）+ worktree + 验收/合并三道硬关卡不依赖 guard |
| Node.js 运行时依赖 | 安装文档明确 + 后续可选 plain-Docker 打包（pi 官方 containerization 三模式之一）；hermes 后端式的容器下沉留作演进 |
| pi 无内置沙箱（设计如此） | 天枢硬保证层就是其官方推荐的宿主隔离位；后续 OS 级沙箱（Landlock/Seatbelt profile，借 grok）按需加档 |
| 单文件 guard 的维护漂移 | guard 与 adapter 同仓同版本发布；guard 配置 schema 用 Pydantic 单一真相生成 |
| 多 provider 带来的模型行为差异 | 验收物是行为契约（测试为准，不依赖特定模型风格）；配对评估提供模型选择的实证数据 |

---

## 九、落地依赖链（pi 默认版，修订自前置文档 §5.8）

```
① PiAdapter MVP（M）
   spawn rpc / prompt / 事件归一(B2 envelope) / agent_settled / collect
   ——不含 guard，先跑通「下发→事件→产出」
② 凭证网关 + scoped token（M）【提前，pi 多模型价值的兑现点】
   baseUrl 重定向 + per-run token + hard cost cap + 归因头
③ tianshu-guard v1（M）
   project_trust=no + tool_call deny/allow（复用 PolicyCompiler→guard JSON）
   + provider 白名单/摘除 + 握手 fail-closed
④ 验收闭环（S）
   验收物执行 + follow_up 回灌循环 + 上限 N + 起居注轨迹
⑤ guard v2：ask 档裁决（M）
   extension_ui_request 反向通道 → 天枢裁决 UI → timeout=拒绝
⑥ 三方合并发布（D2，M）+ 证据保全（D3，S）
⑦ ClaudeCodeAdapter 对照（M）→ 多客卿/多模型配对评估
```

横切项不变（F1 原文回注、E1 重试分类落网关层、E2 bash 段级落 PolicyCompiler+guard 共用、B3 origin、B4 Hooks UI、C1-C3 存储恢复、D1 MemorialCurator、F2 供应链）。

每步验收标准：① 一条真实 edict 经 pi 完成并产出 diff；② 客卿进程内抓不到任何真实 key、超预算网关断供；③ 恶意仓库 `.pi/` 扩展不加载、deny 工具被拦且入账；④ 一次不合格→回灌→合格的完整闭环入起居注；⑤ ask 工具在裁决 UI 出现并可处理；⑥ 冲突进入裁决、快照可复查；⑦ 同一 edict 双客卿/双模型评估报告产出。
