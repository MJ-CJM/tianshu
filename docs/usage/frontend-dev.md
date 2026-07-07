# Lovable 前端与本地后端联调指南

## 前提条件

- 天枢后端已安装并可启动（`pip install ".[cli]"`）
- 已配置 `.env` 文件（至少包含 `TIANSHU_LLM_API_KEY`）
- Node.js 18+ 已安装

## 方式 1 — 拉取 Lovable 代码到本地（推荐）

Lovable 项目关联 GitHub 仓库，可以直接 clone 到本地运行。

### 1.1 克隆前端仓库

```bash
cd ~/tiangong
git clone git@github.com:MJ-CJM/tianshu-web.git
cd tianshu-web
npm install
```

### 1.2 配置 API 地址

在 `tianshu-web/` 根目录创建 `.env.local` 文件：

```env
VITE_API_URL=http://localhost:8000
```

前端代码中使用 `import.meta.env.VITE_API_URL` 获取后端地址。

### 1.3 启动后端

```bash
cd ~/tiangong/tianshu
uvicorn tianshu.app:create_app --factory --host 0.0.0.0 --port 8000
```

### 1.4 启动前端

```bash
cd ~/tiangong/tianshu-web
npm run dev
```

前端通常运行在 `http://localhost:5173`，后端在 `http://localhost:8000`。

后端已配置 CORS `allow_origins=["*"]`，本地联调不会被跨域拦截。

### 1.5 验证联通

```bash
# 后端健康检查
curl http://localhost:8000/health

# 浏览器访问前端
open http://localhost:5173
```

在浏览器 DevTools → Network 面板中确认前端请求正确发到 `localhost:8000/api/*`。

## 方式 2 — 隧道暴露本地后端给 Lovable 在线预览

适用场景：不想在本地跑前端，直接在 Lovable 编辑器的预览环境中测试。

### 2.1 启动后端

```bash
cd ~/tiangong/tianshu
uvicorn tianshu.app:create_app --factory --host 0.0.0.0 --port 8000
```

### 2.2 创建公网隧道

使用 cloudflared（无需注册）：

```bash
npx cloudflared tunnel --url http://localhost:8000
```

或使用 ngrok：

```bash
ngrok http 8000
```

终端会输出一个公网 URL，例如：

```
https://random-name.trycloudflare.com
```

### 2.3 在 Lovable 中配置 API 地址

在 Lovable 编辑器中，将前端代码的 API 基础地址改为隧道 URL：

```typescript
const API_URL = "https://random-name.trycloudflare.com";
```

或通过 Lovable 的环境变量设置 `VITE_API_URL`。

### 2.4 注意事项

- 隧道 URL 每次重启会变化（cloudflared 免费版），需要重新配置
- 隧道有延迟，联调体验不如本地
- ngrok 免费版有连接数限制

## 两种方式对比

| 对比项 | 方式 1（本地运行） | 方式 2（隧道） |
|--------|-------------------|---------------|
| 延迟 | 极低 | 较高 |
| 调试体验 | 好（DevTools 直接看） | 一般 |
| 依赖外部服务 | 否 | 是 |
| 适用场景 | 日常开发联调 | 快速验证 / 展示 |

**推荐日常开发使用方式 1**，功能稳定后再同步到 Lovable。
