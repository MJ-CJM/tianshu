# tianshu

> 天枢是一座会与你共同成长的宫殿。宫殿里有你的分身（emperor）—— 跨会话、跨平台持续演进的个人画像；也有六部官员 —— 各自精进专业，共同辅佐你的目标。任务流转间，官员与分身一起成长。

架构意图与稳定契约见 `docs/design/`，当前代码真相见 `docs/impl/`。

## 外部网络通讯（鸿胪寺）

天枢内置 4 个网络工具，按 profile 差异化启用：

| 工具 | Tier | OFFLINE | DEFAULT | RESEARCH |
|------|------|---------|---------|----------|
| `web_fetch` | T2 | ❌ | ✅ (local+jina) | ✅ (+firecrawl) |
| `web_search` | T2 | ❌ | ✅ (tavily) | ✅ |
| `api_request` | T2/T3 | ❌ | ❌ | ✅ (GET/HEAD) |
| `web_extract` | T2 | ❌ | ❌ | ✅ (firecrawl) |

- `api_request` 的写方法 (POST/PUT/DELETE/PATCH) 在任何 profile 都需在 Edict 中单独 opt-in 并走审批
- 凭证通过藏兵阁的"外部凭证"tab 管理，Fernet 加密，LLM 永不可见
- SSRF / rate-limit / host-whitelist 三道防护

env 要求：
- `TIANSHU_SECRET_MASTER_KEY` — Fernet key，缺失则 api_request 降级不注册
- `TIANSHU_FIRECRAWL_API_KEY` / `TIANSHU_TAVILY_API_KEY` / `TIANSHU_JINA_API_KEY` — 按需

运维手册见 [`docs/ops/credentials.md`](docs/ops/credentials.md)。
