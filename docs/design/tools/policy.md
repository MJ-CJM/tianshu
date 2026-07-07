# 工具治理 — PolicyEngine、规则、SessionRule、审批、Profile

> 设计意图：在 tier 之上做细粒度事前决策，把「人是否授权」变成可持久化、可复用、可审计的数据；长任务不被同类审批反复打断。

## 1. PolicyEngine

`tools/policy.py` 的设计原则：
- `PolicyContext` / `PolicyDecision` 是 frozen dataclass（immutable，并发/测试友好）
- `PolicyRule` 是 Protocol（`rule_id` / `priority` / `evaluate`）→ 便于 fake 注入
- 规则按 priority **降序**执行；`deny` / `require_approval` 短路；`allow` 不短路（允许后续覆盖）
- **fail-open 单条规则**：单规则超时（>1s）或异常 → abstain + WARNING
- **fail-secure 整体引擎**：引擎整体超时（>3s）或未知异常 → deny + ERROR
- 全部弃权 → 默认 allow

`evaluate` 返回 `PolicyDecision(verdict, rule_id, reason, metadata)`——verdict + rule_id + reason 是决策可追溯的最小单元。

## 2. 内建规则管线（build_default_rules）

按 priority 降序：

| 规则 | priority | 职责 |
|---|---|---|
| `TierEscalationRule` | 100 | 读 `edict.runtime.tier_overrides`，**只升不降**；提升即 require_approval |
| `WorkspaceBoundaryRule` | 90 | path/cwd/file_path 越界 → **直接 deny**（不走审批，因审批按钮本身是攻击面） |
| `BashSafetyRule` | 80 | shell 黑名单（`rm -rf /`、`sudo `、fork bomb…）永远 deny；profile 白名单 allow；其余 require_approval |
| `LarkCliSafetyRule` | 80 | 飞书 lark-cli 写操作升级审批 |
| `NetworkSafetyRule` | 75 | 4 个网络工具的 profile 校验 + host 白名单 + 写方法审批（见 network.md） |
| `ApprovalRequiredListRule` | 70 | 命中 `edict.runtime.approval_required_tools` → require_approval |
| `DefaultTierRule` | 10 | 兜底：profile `auto_approve_max_tier` 内放行；T3/T4 默认 require_approval |

黑名单优先级高于 profile 白名单（profile 不能覆盖黑名单）。

## 3. SessionRuleStore — 信任缓存

审批通过可升级为 `SessionRule`，让后续同类调用直接命中放行：

| scope | 实现 | 持久化 |
|---|---|---|
| `edict` | `InMemorySessionRuleStore` | 进程内，任务结束清理 |
| `always` | `SqliteSessionRuleStore`（`session_rules` 表） | 持久，默认 30 天过期 |

`CompositeSessionRuleStore` 先查 in-memory 再查 sqlite。匹配靠 **arg_fingerprint**（按工具选算法）：
- `edit_file`/`write_file` → `dir:{dirname}`（同目录复用）
- `shell_exec`/`bash` → `bash:{前两 token}`（如 `git push`）
- `memory_*` → 过滤 value/content 后的 sorted keys
- 默认 → args 稳定 JSON 的 sha1
- `*` 通配（manual rule 匹配任意 args）

**硬约束**：`assert_can_grant` 禁止 bash 类工具 always scope（攻击面太大），违规降级为 once。

## 4. PolicyHook + ApprovalManager

`PolicyHook`（`BEFORE_TOOL_CALL`，priority=5，先于残留 ApprovalManager handler）委托 PolicyEngine：

```text
on_before_tool_call
  → 解析 tool_tier (缺失=T4)
  → PolicyEngine.evaluate
  → require_approval 时先查 session rule → 命中改 allow (emit policy.session_rule_matched)
  → emit policy.decision (deny 还广播 WS toast)
  → allow: 放行 | deny: HookResult(block) | require_approval: _request_approval
```

`_request_approval` 走 `ApprovalManager`：emit `tool.approval_required`（EventBus.fire 单点持久化 + 广播 WS）→ `wait_for_approval`（asyncio.Event，`APPROVAL_TIMEOUT=300s`）→ 拿到 `Decree`：approve 放行，reject/timeout block。

### ApprovalManager 审批种类

| 接口 | 场景 |
|---|---|
| `wait_for_approval` / `submit_tool_decision` | 执行中 tool-call 审批（不改 memorial 状态，只 unblock） |
| `submit_decree` | 旧式 memorial 审批（approve/reject/retry/amend/cancel，改 memorial 状态） |
| `wait_for_outer_loop_decision` / `submit_outer_loop_decision` | 长任务 outer loop L3 人工决策（独立队列，超时 86400s） |

`submit_tool_decision` 的 `grant_scope`（once/edict/always）决定是否 `_write_session_rule_from_decree` 升级 session rule；always + bash 自动降级 once 并在事件 payload 标 `grant_downgraded`。

## 5. PolicyProfile — 任务级预配

`EdictRuntime.policy_profile` 在任务启动前 proactive 预配权限，解决长任务频繁审批。三个内建模板：

| 模板 | auto_approve_max_tier | bash 白名单 | 网络 |
|---|---|---|---|
| `safe-explore` | T0 | — | OFFLINE |
| `refactor-in-place` | T1 | `git status`/`git diff` | DEFAULT |
| `trusted-automation` | T3 | `git `/`pytest`/`ruff`/`black`/`mypy` | RESEARCH |

`expand_profile_to_rules` 把 profile 展开为 **edict-scope** session rules（allowed_paths → edit/write rules；allowed_bash_prefixes → shell_exec rules）。硬约束：只建 edict scope，不能 always。

## 6. winding_down 副作用拦截

`EdictRuntime.lifecycle_phase == "winding_down"` 时（预算软着陆/收尾），`ToolRegistry.execute` 直接拦截所有 `side_effect=True` 工具，返回错误提示「请改用只读工具完成总结/交接」。这让长任务能在预算耗尽前安全收口，不留半成品副作用。

## 7. 威胁模型 — 每条规则挡哪类攻击

PolicyEngine 不是泛泛的「危险操作拦截器」，每条内建规则对应一类具体攻击面。Agent 输出不可信（prompt 注入、越权工具调用），规则在工具真正执行前做事前裁决。

| 攻击类别 | 典型手法 | 拦截规则（`policy_rules/`） | 裁决 |
|---|---|---|---|
| **路径穿越 / 任意文件读写** | `path="../../etc/passwd"`、绝对路径写出 workspace | `WorkspaceBoundaryRule` | 越界 **直接 deny**（不走审批） |
| **RCE / 系统破坏** | `rm -rf /`、`mkfs`、`dd of=/dev`、fork bomb、`sudo `、`chmod 777 /` | `BashSafetyRule` 黑名单 | **deny**（profile 白名单也不能覆盖） |
| **凭证泄露 / 提权** | `git push --force`、未知 shell 命令外带数据 | `BashSafetyRule` 未命中白名单分支、`LarkCliSafetyRule` 写操作 | require_approval（人在回路） |
| **SSRF / 内网探测** | `api_request` 打内网 host、`web_fetch` 抓未授权地址 | `NetworkSafetyRule`：host 不在 `runtime.api_request_hosts` → deny；profile 未开 fetch/search → deny | deny |
| **数据外带（写方法）** | `api_request` POST/PUT/DELETE/PATCH 把内部数据外发 | `NetworkSafetyRule`：写方法 host 须在 `api_request_write_hosts`，且**强制 require_approval** | require_approval |
| **资源耗尽 / tier 漂移** | 调用比声明更危险的工具、绕过审批配额 | `TierEscalationRule`（只升不降，提升即审批）、`DefaultTierRule`（T3/T4 默认审批）、`ApprovalRequiredListRule` | require_approval |

**设计原则**：能用数据边界（路径、host）判定的「确定性危险」直接 **deny**，不给审批按钮——因为审批按钮本身是社工攻击面（攻击者可诱导用户点「批准」）。需要人类语境判断的灰色操作（未知 bash、外网写）才升级 require_approval。

### 优先级与短路语义

`PolicyEngine._evaluate_inner` 按 priority **降序**逐条 `evaluate`，规则返回 `None` 表示弃权交给下一条：

```text
for rule in sorted(rules, by priority desc):
    decision = rule.evaluate(ctx)            # 单规则超时(>1s)/异常 → 当作弃权 + WARNING
    if decision is None:        continue     # 弃权
    if verdict in (deny, require_approval):  return decision   # ← 短路，立即终止
    if verdict == allow:        last_allow = decision          # 不短路，可被后续覆盖
return last_allow or PolicyDecision(allow, "default")          # 全弃权 → 默认放行
```

- `deny` / `require_approval` **立即短路**——高优先规则（如 WorkspaceBoundary=90）先于低优先规则跑，确定性危险不会被后面的宽松规则翻盘。
- `allow` **不短路**——只记为 `last_allow`，让更低优先级规则仍有机会升级到 deny/approval（例如 BashSafety 白名单 allow 不会压住其它检查）。
- 这就是「黑名单优先于白名单」的实现：BashSafetyRule 内部先扫黑名单 deny（同一规则内短路），profile 白名单只在没命中黑名单时才 allow。
- 整体 fail-secure：引擎超时(>3s)/未知异常 → **deny**；单规则失败 fail-open 弃权，避免一条坏规则瘫痪整条管线。

## 8. session 规则缓存机制（信任的可复用化）

威胁模型解决「该不该拦」，session 缓存解决「拦过一次别反复拦」。一次 `require_approval` 被人工 approve 后，可升级为 `SessionRule` 写入 `SessionRuleStore`，后续**同类**调用在 `PolicyHook` 命中即改判 `allow`（`policy_rules/` 不参与，缓存查询发生在 hook 层、规则裁决之后）：

```text
PolicyEngine → require_approval
  → SessionRuleStore.find_match(tool_name, args, edict_id)
      命中 → verdict 改 allow（emit policy.session_rule_matched）
      未命中 → 走 ApprovalManager 等人工
```

匹配靠 **arg_fingerprint**（`compute_fingerprint` 按工具选算法，见 §3），按「同类」而非「逐字节相等」复用：`edit_file` 用目录粒度、`shell_exec` 用命令前两 token，避免每个文件/每条命令都要重新批一次。`"*"` 通配指纹专供 manual 规则匹配任意 args。

两层 scope 决定信任的持久度与攻击半径：

- **edict scope**（`InMemorySessionRuleStore`）：进程内、`clear_edict` 随任务结束清理——信任不外溢到别的任务。profile 预配（§5）只能建这一层。
- **always scope**（`SqliteSessionRuleStore`）：持久化、默认 30 天过期——只给低风险工具长期免审。
- **硬约束 `assert_can_grant`**：bash 类工具（`shell_exec`/`bash`）**禁止 always scope**（攻击半径太大），违规由上层降级为 once，并在事件 payload 标 `grant_downgraded`。

这样「人审过一次」成为可持久化、可撤销（`revoke`）、可审计（带 `granted_by_decree_id` / `source`）的数据，长任务不被同类审批反复打断，同时把长期信任限制在安全工具上。

## 9. 自定义规则编写指引

新增一条规则只需实现 `PolicyRule` Protocol（`tools/policy.py`）并放进 `policy_rules/`：

```python
@dataclass
class MyRule:
    rule_id: str = "my_rule"
    priority: int = 60          # 决定在管线中的位置（见下）

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        if ctx.tool_name not in MY_TARGET_TOOLS:
            return None         # 不相关 → 弃权，别返回 allow（会压住后续规则）
        if <确定性危险>:
            return PolicyDecision("deny", self.rule_id, reason, metadata={...})
        if <需要人判断>:
            return PolicyDecision("require_approval", self.rule_id, reason)
        return None             # 让 DefaultTierRule 兜底
```

编写约定：

- **不相关就返回 `None`**（弃权），不要返回 `allow`——`allow` 会成为 `last_allow`，可能压住本应升级的判定。只有「我确信此调用安全且想抑制后续规则」才返 `allow`。
- **priority 选位**：要在某条规则之前生效就取更高值。内建坐标：WorkspaceBoundary=90 > Bash/Lark=80 > Network=75 > ApprovalRequiredList=70 > DefaultTier=10（兜底，永远最后）。
- **确定性危险用 deny、灰色操作用 require_approval**——deny 不给审批按钮（见 §7 原则）。
- **evaluate 必须快**（<1s）且**纯**（只读 `ctx`，不产生副作用）——超时/抛异常会被当作弃权（fail-open）。需要 I/O 时务必加超时。
- **挂载**：把实例加进 `build_default_rules()`（`policy_rules/__init__.py`）即纳入默认管线；引擎构造时自动按 priority 降序排序。

**相关实现**：[../../impl/tools/](../../impl/tools/)
**相关运行时**：审批如何接入执行链路见 [../runtime-flow.md](../runtime-flow.md) §「审批层级」。
