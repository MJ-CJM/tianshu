# CLI 命令体系

> `tianshu` CLI 基于 typer，是后端 HTTP API 的薄客户端——不直连 DB，全部经 `/api`（与 Web 同源契约）。本篇对照 `src/tianshu/cli/` 核实。

## 1. 设计判断

| 判断 | 取舍 |
|---|---|
| 薄客户端 | CLI 仅做「构造请求 + 渲染输出」，业务逻辑全在后端；`cli/client.py` 封装 `api_get/post/put/delete` |
| 同源 | 默认连 `TIANSHU_API_URL`（缺省 `http://localhost:8000`），与 Web 走同一组 API |
| 输出双格式 | 多数命令支持 `--format table|json`（table 用 rich，json 直出）|
| 子命令分组 | 按领域用 `typer.Typer` 子应用挂载（`app.add_typer`），命令族清晰 |

## 2. 入口与装配（`cli/main.py`）

`app = typer.Typer(name="tianshu")`，挂载各子应用：

| 命令族 | 子命令 | 用途 |
|---|---|---|
| `tianshu edict` | `submit` / `get` / `list` | 下旨、查任务、列表（定时任务改用对话内 `schedule_edict` 工具） |
| `tianshu memorial` | `get` / `list` / `review` | 查奏折、列表、人工审批 |
| `tianshu config` | `list` / `get` / `add` / `set` / `rm` / `activate` | LLM 配置管理 |
| `tianshu decree` | `submit` / `list` | 批红记录 |
| `tianshu event` | `list` | 查事件 |
| `tianshu schedule` | `list` / `cancel` | 定时 job |
| `tianshu cost` | `summary` / `budget` / `records` | 成本 |
| `tianshu provider` | `list` / `status` | provider |
| `tianshu plugin` | `list` | 插件 |
| `tianshu dag` | `show` / `cancel` / `retry` | DAG 执行 |
| `tianshu worker` | `list` / `status` | WorkerPool |
| `tianshu health` | （顶层命令） | 健康检查 |
| `tianshu watch` | （顶层命令） | 实时跟踪/订阅事件流 |

> `health` 与 `watch` 是 `app.command()` 直接注册的顶层命令，不属任何子应用。

## 3. HTTP 客户端（`cli/client.py`）

| 函数 | 行为 |
|---|---|
| `api_get/post/put/delete` | httpx 同步请求，超时 360s；`ConnectError` → 友好报错退出码 1；`HTTPStatusError` → 打印状态码与响应体退出 |
| `get_client` | 返回可复用 httpx.Client（watch 等长连场景） |
| `_base_url` | 读 `TIANSHU_API_URL` env，默认 localhost:8000 |

## 4. 输出约定

- 命令解析后端 `ApiResponse` 的 `data` 字段渲染；列表读 `metadata.total`。
- table 模式用 `rich.table.Table` / `rich.console.Console`，状态用颜色标注（如 completed=green、failed=red）。
- `--format json` 时 `console.print_json` 原样输出，便于管道处理。

## 5. 边界

- CLI 不维护本地状态，每次调用都是无状态 HTTP 请求。
- 需后端服务在线；连不上即退出码 1。
- 定时/周期任务不走 `edict submit`，由对话内工具创建（命令 docstring 已注明）。

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)
