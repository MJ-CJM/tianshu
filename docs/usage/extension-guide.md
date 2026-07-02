# 扩展开发指南

二次开发的五类扩展点：Tool / 自定义 MCP Server / Provider / Plugin / Channel。每节给最小端到端示例 + 落点路径。设计意图见各 [`../design/`](../design/)，实现细节见各 [`../impl/`](../impl/)。

> 本篇是「怎么写」；偏内核职责分工的入口清单见 [developer-guide.md](developer-guide.md)。

## 1. 扩展工具（Tool）

工具是 LLM 能调用的原子能力。用 `ToolDefinition` 声明 schema + 治理字段，注册进 `ToolRegistry`，之后自动经 `PolicyEngine` / 审批治理，无需自己写权限逻辑。

- 落点：内建工具放 `src/tianshu/tools/builtins.py`；注册中心 `tools/registry.py`。
- 治理字段：`tier`（T0-T4 风险分级，决定是否需审批）、`side_effect`（True 在 winding_down 阶段被拦）、`max_result_chars`（结果自动截断）。

最小端到端（参照 builtins.py 现有 `shell_exec` / `read_file` 写法）：

```python
from tianshu.tools.registry import ToolDefinition
from tianshu.tools.types import ToolResult, ToolTier, ok_result

async def word_count(text: str) -> ToolResult:
    return ok_result(str(len(text.split())))

registry.register(
    "word_count",
    word_count,
    ToolDefinition(
        name="word_count",
        description="Count whitespace-separated words in text.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        tier=ToolTier.T0_READONLY.value,  # 只读 → 走快路径、免审批
        side_effect=False,
    ),
)
```

handler 必须是 `async`、返回 `ToolResult`；`execute` 会先按 `parameters` 做 jsonschema 校验并丢弃未声明字段。详见 [../design/tools/registry.md](../design/tools/registry.md)、[../design/tools/policy.md](../design/tools/policy.md)、[../impl/tools/README.md](../impl/tools/README.md)。

## 2. 自定义 MCP Server

天枢是 MCP **client**：把外部 MCP server 暴露的工具拉进 `ToolRegistry`，工具名编码为 `<server>__<tool>`，统一受 tier 治理。接外部 server 靠配置而非写代码。

- 落点：YAML 种子 `~/.tianshu/mcp_servers.yaml`（顶级键 `mcp_servers`，name 在 dict key 上）；DB 覆盖表 `mcp_server_overrides`（运行时增改）。
- 装配：`app.py` lifespan 构造 `MCPManager(tools, storage=storage)` 并后台 `start()` 并行拉起所有 enabled server。

最小端到端 — 加一个 stdio server：

```yaml
# ~/.tianshu/mcp_servers.yaml
mcp_servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
    default_tier: 1            # 该 server 工具的默认风险分级
    tool_overrides:
      delete_file: 3           # 个别危险工具单独提级
    tools:
      include: []              # 空 = 全开；可填白名单
      exclude: ["dangerous_op"]
    env:
      TOKEN: "${MY_TOKEN}"     # ${VAR} 从环境变量插值，缺失保留字面量
```

streamable_http server 改填 `transport: streamable_http` + `url` + `headers`。`default_tier == 0` 视为只读、`side_effect=False`；其余 tier 保守置 `side_effect=True`。配置 schema 见 `tools/mcp/config.py`（`MCPServerConfig`），机制见 [../design/tools/mcp.md](../design/tools/mcp.md)。

## 3. 加 LLM Provider

Provider 是一份带能力/配额/价格的模型配置。`ProviderManager` 负责选路（能力匹配 + 优先级）、限速与 fallback，注册即落库。

- 落点：`providers/`（LiteLLM 适配 + `ProviderInfo` 契约）+ `config_manager.py`（多配置）。

最小端到端：

```python
from tianshu.providers.capabilities import ProviderInfo, ProviderCapability

provider_manager.register(ProviderInfo(
    name="my-gpt",
    model="gpt-4o-mini",
    api_base=None,
    capabilities=[ProviderCapability.CHAT, ProviderCapability.STREAMING],
    status="active",
    priority=0,                # 越小越优先
    rpm_limit=60,
    cost_per_1k_prompt=0.15,
    cost_per_1k_completion=0.60,
))
```

注册落 `providers` 表（`save_provider`）。日常的多模型配置走 `ConfigManager` + `sync_from_config` 自动同步，直接 `register` 适合插件/程序化注入。详见 [../design/llm/client.md](../design/llm/client.md)、[../impl/llm/README.md](../impl/llm/README.md)。

## 4. 写插件（Plugin）

插件用一份 `manifest.json` 声明身份，经 `PluginApi` 统一门面把上面各类能力注册进内核，无需改装配代码。

- 落点：仓库根 `plugins/<name>/manifest.json`（`PluginLoader` 扫描发现）；门面 `plugins/api.py`。

最小端到端 — 清单 + 能力注入：

```json
// plugins/wordcount/manifest.json
{
  "name": "wordcount",
  "version": "1.0.0",
  "type": "tool",
  "entry_point": "wordcount.plugin:setup",
  "permissions": [],
  "sha256": ""
}
```

```python
# 插件入口在自身加载逻辑里，用注入的 PluginApi 注册能力
def setup(api):  # api: PluginApi
    api.register_tool("word_count", word_count, schema)     # → ToolRegistry
    api.register_channel(MyChannel())                       # → ChannelRegistry
    api.register_provider(my_provider_info)                 # → ProviderManager
    api.register_hook(HookType.AFTER_TOOL_CALL, my_handler, priority=100)
```

`app.py` lifespan 启动时 `PluginLoader(plugins_dir).discover()` 解析每个清单并 `register_plugin` 登记落库（**只读清单、不执行插件代码**）。把 `type` 映射到 `entry_point` 真正注入能力，由插件入口或装配处的分派完成。注册的工具/渠道与内建能力等价，同受治理链约束。详见 [../design/plugins/README.md](../design/plugins/README.md)、[../impl/plugins/README.md](../impl/plugins/README.md)。

## 5. 扩展通知渠道（Channel）

渠道是诏令/事件的外发出口。继承抽象基类实现 `name` + `send`，注册进 `ChannelRegistry`（自带 per-channel 限速）。

- 落点：`notifier/channels/`，继承 `base.py` 的 `NotificationChannel`；注册中心 `notifier/channel_registry.py`。已有 `FeishuChannel` / `DingTalkChannel` / `EmailChannel` 可参照。

最小端到端：

```python
from tianshu.notifier.channels.base import NotificationChannel

class WebhookChannel(NotificationChannel):
    @property
    def name(self) -> str:
        return "webhook"

    async def send(self, message: dict, rendered: str) -> bool:
        # rendered = 已渲染文本；message = 原始事件 payload
        ...  # POST 出去，成功返回 True
        return True

channel_registry.register(WebhookChannel(), rpm=10)  # rpm = 每分钟上限
```

`send_all` / `send_to` 会按 `name` 调度并跳过限速渠道；异常被吞为 `False` 不影响其他渠道。详见 [../design/interfaces/channels.md](../design/interfaces/channels.md)、[../impl/interfaces/README.md](../impl/interfaces/README.md)。
