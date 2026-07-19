# Web 前端与本地后端联调指南

> ⚠️ v0.4.2 无统一鉴权，只支持可信本地联调。禁止使用公网隧道或把后端监听到不可信网段；安全远程访问属于 G1 规划能力。

## 前提条件

- 已安装天枢后端与 CLI 依赖（`pip install -e ".[cli]"`）
- 已配置 `.env`（至少包含 `TIANSHU_LLM_API_KEY`）
- Node.js 20+

## 1. 启动本地后端

在仓库根目录运行：

```bash
uvicorn tianshu.app:create_app --factory --host 127.0.0.1 --port 8000
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

如需让其他设备或在线设计工具访问，请先完成 G1 的统一鉴权和安全远程访问边界；v0.4.2 不提供这项公开部署承诺。
