# CLI 命令体系

> `tianshu` CLI 基于 typer。任务与治理命令主要复用后端 HTTP API；`doctor`、
> `secrets`、`evals`、`keqing/shadow` 等运维或实验命令会读取本地配置、数据库或工作区，
> 不能一概视为无状态 API 薄客户端。本篇对照 `src/tianshu/cli/` 核实。

## 1. 设计判断

| 判断 | 取舍 |
|---|---|
| API 优先 | Edict、Memorial、成本等用户操作走后端；本地运维命令明确单独标识 |
| 同源 | 默认连 `TIANSHU_API_URL`（缺省 `http://localhost:8000`），与 Web 走同一组 API |
| 输出双格式 | 多数命令支持 `--format table|json`（table 用 rich，json 直出）|
| 子命令分组 | 按领域用 `typer.Typer` 子应用挂载（`app.add_typer`），命令族清晰 |

## 2. 入口与装配（`cli/main.py`）

`app = typer.Typer(name="tianshu")`，挂载各子应用：

| 命令族 | 子命令 | 用途 |
|---|---|---|
| `tianshu edict` | `submit` / `get` / `list` | 下旨、查任务、列表（定时任务改用对话内 `schedule_edict` 工具） |
| `tianshu memorial` | `get` / `list` / `review` | 查奏折、列表、人工审批 |
| `tianshu auth` | `login` / `logout` / `whoami` | PAT 换短期 CLI session、注销与身份确认 |
| `tianshu config` | `list` / `get` / `add` / `set` / `rm` / `activate` | LLM 配置管理 |
| `tianshu decree` | `submit` / `list` | 旧式 Decree 兼容记录（用户概念为“裁决”） |
| `tianshu event` | `list` | 查事件 |
| `tianshu schedule` | `list` / `cancel` | 定时 job |
| `tianshu cost` | `summary` / `budget` / `records` | 成本 |
| `tianshu provider` | `list` / `status` | provider |
| `tianshu plugin` | `list` | 实验插件清单（不代表代码已加载） |
| `tianshu dag` | `show` / `cancel` / `retry` | DAG 执行 |
| `tianshu worker` | `list` / `status` | WorkerPool |
| `tianshu evals` | `run` / `sample` / `sets` / `runs` / `show` / `failures` / `backfill` | 本地平台回归评测与归因 |
| `tianshu secrets` | `gen-key` / `rotate-master-key` | 本地主密钥生成与数据库密文轮换 |
| `tianshu workspace` | `status` / `preview` / `approve` / `apply` | 受治理工作区预览与合入 |
| `tianshu keqing` / `tianshu shadow` | `agents`；`list` / `revert` | 实验执行器枚举与本地影子快照 |
| `tianshu health` | （顶层命令） | 健康检查 |
| `tianshu doctor` | （顶层命令） | 默认只读、零外网装机诊断；`--llm` 才做真实调用 |
| `tianshu watch` | （顶层命令） | 实时跟踪/订阅事件流 |

> `health`、`doctor` 与 `watch` 是顶层命令，不属任何子应用。

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

- API 命令需要后端在线；`auth` session 默认保存在 owner-only
  `~/.tianshu/credentials.json`，非 loopback 凭证传输必须使用 HTTPS。
- 本地运维/实验命令可能直接访问配置、SQLite 或工作区，执行前应确认它们指向预期实例。
- 定时/周期任务不走 `edict submit`，由对话内工具创建（命令 docstring 已注明）。

**相关实现**：[../../impl/interfaces/](../../impl/interfaces/)
