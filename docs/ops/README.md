# 运维层

部署、凭证与渠道接入指南。先看
[当前实现与支持边界](../CURRENT-STATE.md)，快速启动见
[getting started](../usage/getting-started.md)。当前运维口径只覆盖单主机、
single-node SQLite；历史方案不能替代这里的边界。

| 文档 | 内容 |
|---|---|
| [runtime-boundaries.md](runtime-boundaries.md) | 源码/Wheel/Docker、trusted-local/secure-remote、MCP 与非承诺 |
| [observability.md](observability.md) | Edict、run/attempt、SystemAudit、Scheduler/notification 排查 |
| [credentials.md](credentials.md) | 凭证与密钥管理 |
| [feishu-setup.md](feishu-setup.md) | 飞书应用接入配置 |
| [feishu-assistant-mode.md](feishu-assistant-mode.md) | 飞书助手模式操作指南 |
| [telegram-setup.md](telegram-setup.md) | Telegram 机器人接入配置 |
| [multi-bot.md](multi-bot.md) | 同一后端挂多个独立 bot 实例 |
| [mcp_servers.yaml.example](mcp_servers.yaml.example) | MCP 服务器配置示例 |

管理面访问要求：

- 普通 PAT 只操作自己提交的任务及其派生资源；
- SystemAudit、全局 audit/network、Worker、配置、记忆和全局成本需要 `admin`；
- trusted-local 主人拥有 admin scope，但这不代表任意远程调用都可信；
- secure-remote 必须完成 HTTPS public URL、精确 host/origin、PAT/会话与 proxy 边界配置。
