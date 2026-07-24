"""tianshu-guard 配置的单一真相(Pydantic)+ 最小 PolicyCompiler。

tianshu-guard 是天枢维护、随 adapter 版本钉死的 pi 扩展(TS),经 `-e` 注入 pi 进程做
**进程内软增强**治理:project_trust=no(拒载被开发仓 .pi/ 扩展)、tool_call deny/allow
(bash 段级不对称)、registerProvider 把 baseUrl 重定向到天枢网关 + 用 scoped token、
registerCommand 握手(fail-closed)。

本文件是配置的 Python 单一真相:PolicyCompiler 把天枢 policy 编译成 GuardConfig
(tighten-only:只可能比 policy 更严),序列化成 JSON 供 guard TS 读取;产物 + policy
版本入起居注。硬保证不寄托于 guard(guard 失效时网关/worktree/验收三关卡兜底)。

guard TS 侧的运行时匹配(bash 段级、provider 重定向)镜像 compile_bash_rules 的语义。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

GUARD_CONFIG_VERSION = "1"


class BashRules(BaseModel):
    """bash 命令段级不对称匹配规则(E2 语义)。

    deny:逐段 + 全串双检(链式命令任一段命中即拒);allow:仅全串匹配
    (防 `git status && rm -rf /` 借 `git *` allow 整条放行);不可拆(子 shell/命令
    替换)一律升 ask。
    """

    deny_segments: tuple[str, ...] = ()  # glob,逐段+全串匹配
    allow_exact: tuple[str, ...] = ()  # glob,仅整串匹配
    unsplittable_action: str = "ask"  # 子shell/命令替换等不可拆时的兜底档


class GuardConfig(BaseModel):
    """注入 guard TS 的完整配置。tighten-only:字段只收紧不放宽。"""

    version: str = GUARD_CONFIG_VERSION
    project_trust: str = "no"  # 拒载被开发仓 .pi/ 扩展(防提权)
    tool_deny: tuple[str, ...] = ()  # 按 tool 名 deny(如 web_fetch)
    tool_ask: tuple[str, ...] = ()  # 需上报批红的 tool
    bash: BashRules = Field(default_factory=BashRules)
    provider_allowlist: tuple[str, ...] = ()  # 允许的 provider(空=全拒,fail-closed)
    model_allowlist: tuple[str, ...] = ()  # 允许的 model
    gateway_url: str | None = None  # registerProvider 重定向目标(客卿只走网关)
    edict_id: str = ""
    run_id: str = ""
    handshake_required: bool = True  # spawn 后须握手,失败 fail-closed 终止 run

    def to_guard_json(self) -> str:
        return self.model_dump_json(exclude_none=False)


# --- 最小 PolicyCompiler(tighten-only) ---


class PolicyInputs(BaseModel):
    """从天枢 policy 提取的、编译 guard 所需的最小输入。"""

    edict_id: str
    run_id: str
    deny_tools: tuple[str, ...] = ()
    ask_tools: tuple[str, ...] = ()
    bash_deny: tuple[str, ...] = ()
    bash_allow: tuple[str, ...] = ()
    provider_allowlist: tuple[str, ...] = ()
    model_allowlist: tuple[str, ...] = ()
    gateway_url: str | None = None


def compile_guard_config(inputs: PolicyInputs) -> GuardConfig:
    """把 policy 输入编译成 GuardConfig。tighten-only 不变量:

    - project_trust 恒为 "no"(不可被放宽);
    - 空 provider_allowlist → 客卿无可用 provider(fail-closed),不静默放行;
    - deny/ask/bash 规则原样下发(编译不放宽,只可能因 policy 更严而更严)。
    与 A1 下发前策略编译共用同一 policy 语义,保证 Native 审批与下发口径一致。
    """
    return GuardConfig(
        project_trust="no",
        tool_deny=tuple(inputs.deny_tools),
        tool_ask=tuple(inputs.ask_tools),
        bash=BashRules(
            deny_segments=tuple(inputs.bash_deny),
            allow_exact=tuple(inputs.bash_allow),
            unsplittable_action="ask",
        ),
        provider_allowlist=tuple(inputs.provider_allowlist),
        model_allowlist=tuple(inputs.model_allowlist),
        gateway_url=inputs.gateway_url,
        edict_id=inputs.edict_id,
        run_id=inputs.run_id,
        handshake_required=True,
    )
