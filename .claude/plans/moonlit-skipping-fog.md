# 计划：Agent 参数 + 工作目录纳入运行时设置

## Context

当前 `agent_max_iterations`、`agent_timeout_seconds`、`skills_char_budget` 只能通过环境变量设置，运行时不可调；工具沙箱绑定单一 `workspace_dir`，无法动态扩展。用户希望这些参数都能在 Web 设置 Drawer 中查看和修改，默认值来自环境变量。

## Step 1：`config_manager.py` — 新增 `AgentSettings` + ConfigManager 扩展

```python
@dataclass(frozen=True)
class AgentSettings:
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    skills_char_budget: int = 30000
    workspace_dirs: tuple[str, ...] = ()  # frozen 需要 tuple
```

ConfigManager 扩展：
- `__init__` 新增 `agent_settings: AgentSettings` 参数
- `_agent_settings` 字段，复用已有 `_lock`
- `agent_settings` 只读属性
- `update_agent_settings(**kwargs) -> AgentSettings`（不可变替换，同 `_update_locked` 模式）

## Step 2：`models.py` — 新增 API 模型

```python
class AgentSettingsResponse(BaseModel):
    agent_max_iterations: int
    agent_timeout_seconds: int
    skills_char_budget: int
    workspace_dirs: list[str]

class AgentSettingsUpdateRequest(BaseModel):
    agent_max_iterations: int | None = Field(default=None, ge=1, le=100)
    agent_timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    skills_char_budget: int | None = Field(default=None, ge=1000, le=100000)
    workspace_dirs: list[str] | None = None
```

## Step 3：`gateway.py` — 新增 2 个端点

- `GET /api/settings` → 返回 `AgentSettingsResponse`
- `PUT /api/settings` → 接收 `AgentSettingsUpdateRequest`，校验 `workspace_dirs`（路径必须存在且为目录），更新后返回

## Step 4：`app.py` — 初始化 AgentSettings

从 `TianshuSettings` 构造 `AgentSettings`，传给 `ConfigManager`：
```python
agent_settings = AgentSettings(
    agent_max_iterations=settings.agent_max_iterations,
    agent_timeout_seconds=settings.agent_timeout_seconds,
    skills_char_budget=settings.skills_char_budget,
    workspace_dirs=(str(Path(settings.workspace_dir).resolve()),),
)
config_manager = ConfigManager(initial_state, agent_settings=agent_settings)
```

工具注册改为传 `config_manager`：
```python
register_builtins(tools, config_manager)
```

## Step 5：`path_utils.py` — `safe_path` 支持多目录

```python
def safe_path(workspaces: Sequence[Path], path_str: str) -> Path:
    primary = workspaces[0]
    resolved = (primary / path_str).resolve()
    for ws in workspaces:
        ws_resolved = ws.resolve()
        prefix = str(ws_resolved) + os.sep
        if resolved == ws_resolved or str(resolved).startswith(prefix):
            return resolved
    raise PermissionError(f"Path '{path_str}' is outside allowed workspaces")
```

## Step 6：`builtins.py` + 4 个工具文件 — 使用 ConfigManager

`register_builtins(registry, config_manager)` 签名变更。所有工具在执行时动态获取 workspace_dirs：

```python
def register_builtins(registry: ToolRegistry, config_manager: ConfigManager) -> None:
    def _workspaces() -> list[Path]:
        return [Path(d) for d in config_manager.agent_settings.workspace_dirs]

    async def read_file(path: str) -> ToolResult:
        file_path = safe_path(_workspaces(), path)
        ...
```

4 个新工具文件同理：`register_xxx(registry, get_workspaces)` 接收 callable。

## Step 7：`agent.py` + `gateway.py` — 消费 AgentSettings

- `agent.py:100` — `range(self._config_manager.agent_settings.agent_max_iterations)`
- `gateway.py:83` — `config_manager.agent_settings.agent_timeout_seconds`
- Agent 构造函数去掉 `settings: TianshuSettings` 参数（不再直接依赖）

## Step 8：前端 — types + API + hook

`web/src/api/types.ts` 新增：
```typescript
export interface AgentSettings {
  agent_max_iterations: number;
  agent_timeout_seconds: number;
  skills_char_budget: number;
  workspace_dirs: string[];
}
export type AgentSettingsUpdateRequest = Partial<AgentSettings>;
```

`web/src/api/config.ts` 新增 `getAgentSettings()` + `updateAgentSettings()`
`web/src/hooks/useConfig.ts` 新增 `useAgentSettings()` + `useUpdateAgentSettings()`

## Step 9：前端 — `AppSidebar.tsx` 设置 Drawer 新增"执行参数"区块

在"外观"和"LLM 配置"之间插入，布局：

```
外观: [浅色 | 深色]
────────────────────
执行参数
  最大迭代轮数    [InputNumber 1-100]
  执行超时(秒)    [InputNumber 10-3600]
  技能上下文预算   [InputNumber 1000-100000]
  [应用]
────────────────────
工作目录
  /Users/xxx/project   [删除]
  /Users/xxx/data      [删除]
  [+ 添加目录]
────────────────────
LLM 配置
  ...
```

工作目录区块：列表 + 删除按钮 + 添加 Input，第一个目录（主目录）不可删除。

## 关键文件

| 文件 | 改动 |
|------|------|
| `src/tianshu/config_manager.py` | 新增 `AgentSettings`，ConfigManager 扩展 |
| `src/tianshu/models.py` | 新增 `AgentSettingsResponse`、`AgentSettingsUpdateRequest` |
| `src/tianshu/gateway.py` | 新增 `GET/PUT /api/settings` |
| `src/tianshu/app.py` | 初始化 AgentSettings，`register_builtins` 改传 config_manager |
| `src/tianshu/tools/path_utils.py` | `safe_path` 支持多目录 |
| `src/tianshu/tools/builtins.py` | 签名改为 config_manager，动态 workspace |
| `src/tianshu/tools/edit_file.py` | 签名改为 get_workspaces callable |
| `src/tianshu/tools/list_dir.py` | 同上 |
| `src/tianshu/tools/grep.py` | 同上 |
| `src/tianshu/tools/find_files.py` | 同上 |
| `src/tianshu/agent.py` | 从 config_manager 读取 agent 参数 |
| `web/src/api/types.ts` | 新增 AgentSettings 类型 |
| `web/src/api/config.ts` | 新增 API 函数 |
| `web/src/hooks/useConfig.ts` | 新增 hooks |
| `web/src/components/layout/AppSidebar.tsx` | 新增"执行参数"+"工作目录"UI |

## 验证

1. `python -c "from tianshu.config_manager import AgentSettings"` 无导入错误
2. 启动服务 → `curl http://localhost:8000/api/settings` 返回默认值
3. `curl -X PUT /api/settings -d '{"agent_max_iterations": 5}'` → GET 确认已变
4. `curl -X PUT /api/settings -d '{"workspace_dirs": ["/tmp", "/nonexist"]}'` → 校验失败
5. Web 设置 Drawer 调整参数 + 添加/删除目录，功能正常
6. `npx tsc --noEmit` + `npx vite build` 前端构建通过
