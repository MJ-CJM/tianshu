# ADR-0011:客卿执行器与影子快照的治理边界

- 状态:已接受
- 日期:2026-07-08(迭代 3.5「客卿」)
- 相关:[ADR-0002](0002-positioning-superior-office-of-claude-code.md)(上级机关定位)、[ADR-0010](0010-jinyiwei-runtime-defense-in-depth.md)(锦衣卫)、spec §四

## 背景

天枢定位「Claude Code 的上级机关」。MCP server(迭代 1)让 Claude Code 能给天枢
下旨;**双向互操作**还差反方向——天枢派 Claude Code/Codex 出工。这既是能力
(执行面可插拔),也是宣发主卖点(「敢放手」的名场面:视频里天枢驱动 Claude Code)。

2026-07-02 曾评估排除 multica 那种"纯控制平面、自己不执行"的定位。客卿**不冲突**:
天枢是"自执行引擎 + 可选外部执行器",自研引擎仍是默认,定位无让渡。

## 决策

### 1. 客卿是 `AgentResult` 的 drop-in,不是新管线

`Edict.runtime.executor` = `native`(默认)或 `keqing:<agent>`。执行器路由发生在
`Executor.execute_edict` 内一个点:keqing backend → `KeqingExecutor.execute()`,
否则 → `Agent.execute()`。**两者都返回 `AgentResult`**,下游(memorial 生命周期/
事件/hooks/审计/批红)完全不变。这样客卿"只换执行面,不换治理面"。

### 2. headless CLI(v1),ACP 留 v2

v1 用非交互 CLI(`claude -p --output-format stream-json` / `codex exec --json`)而非
ACP 协议:CLI 零额外依赖、覆盖宣发 demo 足够。ACP 深度集成(双向流式、外部 agent
权限请求实时桥接到天枢批红)留 v2(2027 初与 A2A 一起评估)。

### 3. 治理集成四件套(客卿受节制的具体形式)

- **隔离工作区**:每 edict 一个独立目录(`~/.tianshu/keqing/<edict_id>`),客卿改
  这里的文件,不碰主工作区。
- **clean-env**:复用锦衣卫 clean-env,但白名单是**客卿自身**的鉴权变量
  (`ANTHROPIC_API_KEY` 等)——客卿用**它自己**的凭证跑,天枢的 `TIANSHU_*` secrets
  一律不进子进程。这是干净的信任分离:客卿的额度是客卿的,不烧天枢的 key。
- **预算熔断**:解析 stream-json 的 `total_cost_usd`(×7.2 折 CNY),超
  `cost_budget_cny` 即杀进程。
- **产出归一 + 出站脱敏**:客卿最终文本走 `redact_text`(它可能在输出里回显读到的
  secret),再落 memorial → 照走审计 → 批红。

### 4. 影子快照:独立 GIT_DIR,绝不碰用户 .git

"敢放手"需要一条随时可回滚的退路。但天枢**无权**在用户的版本库里提交/回滚
(用户可能有正经 git 历史)。方案:快照仓的 `.git` 放在工作区**之外**
(`~/.tianshu/shadow/<edict>/gitdir`),用 `git --git-dir=<shadow> --work-tree=<work>`
操作——工作区里不出现 `.git`。

revert 用 `read-tree` + `checkout-index` + `clean` 让工作区**精确**匹配目标快照
(含删除该快照之后新增的文件——`checkout -- .` 做不到),并提交一个 revert 节点
保持时间线线性(快照不丢、可再向前)。git 不可用时优雅降级(快照是退路,不是
执行前置,失败只告警不阻断)。

最小版只做**文件系统状态**;进程/DB 状态的完整快照留迭代 5。

## 影响

- 新增 `tianshu.executor.keqing` 包(adapter 注册表 + KeqingExecutor)与
  `tianshu.executor.shadow_snapshot`。
- `EdictRuntime` 加 `executor` 字段(默认 `native`,向后兼容)。
- Codex 适配器的 JSONL 解析按当前理解实现,字段随 CLI 版本演进——标注"生产前
  按实际版本校准",不假装稳定。
- 影子快照 revert 是覆盖工作区的写操作,但独立于用户 .git 且回滚可逆(留新节点),
  故不设批红门;所有 revert 留事件账本。
