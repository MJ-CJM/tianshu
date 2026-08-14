# Web 前端与本地后端联调指南

> v0.4.2 默认使用 `trusted-local`，只适合回环地址上的本地联调。HTTP、WebSocket 与 MCP
> 已共用身份边界；`secure-remote` 支持登录会话、PAT、管理员 scope 和任务所有权隔离，
> 但尚未形成公网生产部署承诺。不要用公网隧道暴露默认开发服务。

## 前提条件

- 已安装天枢后端与 CLI（CLI 已属于基础依赖：`pip install -e .`）
- 已配置 `.env`（至少包含 `TIANSHU_LLM_API_KEY`）
- Node.js 20+

## 1. 启动本地后端

在仓库根目录运行：

```bash
tianshu serve --host 127.0.0.1 --port 8000
```

用健康检查确认后端已就绪：

```bash
curl http://127.0.0.1:8000/health
```

## 2. 启动仓库内前端

另开一个终端：

```bash
cd web
npm install
npm run dev
```

访问 `http://localhost:7999`。当前 Vite 配置会把 `/api`、`/health` 和 WebSocket 请求代理到 `http://127.0.0.1:8000`，无需公开后端端口或另配跨域地址。

## 3. 验证主链路

在浏览器开发者工具的 Network 面板确认：

1. `GET /health` 返回成功；
2. `/api/*` 请求由 7999 代理到本地后端；
3. WebSocket 连接保持在本地；
4. 控制台没有新的错误。

首次使用时，根路由会先读取服务端 onboarding 状态：没有任务的全新实例进入
`/onboarding`，确认当前 demo/live profile 后创建首个治理任务；创建成功后直接进入该
任务详情。读取 onboarding 状态失败时页面显示可重试错误，不会用旧缓存静默跳转。

如确需在受控反向代理后联调，必须显式使用 `secure-remote`，配置 HTTPS public URL、
精确 Host/Origin、bootstrap token hash 与可信代理网段，并通过 `tianshu auth login`
登录。具体变量见 [getting-started.md](getting-started.md)。这仍不等于官方公网部署支持。
