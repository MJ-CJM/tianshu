# 外部凭证运维手册

> Spec: [2026-04-22-external-network-capability-expansion-design.md](../superpowers/specs/2026-04-22-external-network-capability-expansion-design.md) §4

## 生成主密钥

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

写入 `.env` 或部署配置：

```
TIANSHU_SECRET_MASTER_KEY=<generated-key>
```

**⚠️ 丢失主密钥 = 所有已存凭证不可恢复**。务必备份到密钥管理器 (1Password / AWS Secrets Manager / etc)。

## 添加凭证

1. 登录后进入「藏兵阁 → 外部凭证」
2. 点「新增凭证」，填：
   - **名称** — 人类可读，如 `github-prod-token`
   - **匹配域** — 精确 `api.github.com` 或通配 `*.notion.com`
   - **Header 模板** — 默认 `Authorization: Bearer {value}`；`{value}` 是占位符
   - **Value** — 真实凭证值（password 输入，不回显）

## 轮换凭证

UI 编辑同一条凭证，替换 value 字段即可。`host_pattern` / `header_template` 不可改（需改动请删除重建）。

## 删除凭证

删除前系统检查有无活跃 Edict 在 `api_request_hosts` / `api_request_write_hosts` 引用该 host。有引用会返回 409，需要先从 Edict 移除再删除。

## 在 Edict 中启用 api_request

1. 创建 Edict 时 profile 选 `trusted-automation`
2. 展开"网络能力"section
3. 在"允许调用的 API host"选择或输入需要的 host（已有凭证的 host 会出现在下拉里）
4. 如需写方法，勾选"允许写方法"并选定 write host 子集

## 降级场景

| 场景 | 行为 |
|------|------|
| 主密钥未设置 | `api_request` 工具自动不注册；其他 3 个网络工具不受影响 |
| DB 迁移失败 | Storage init 抛错，应用停止启动 |
| 单个凭证解密失败 | 对应 api_request 调用返回 `credential_conflict` 错误；其他凭证不受影响 |
| `TIANSHU_FIRECRAWL_API_KEY` 缺失 | `web_extract` + firecrawl engine 不注册；web_fetch 仍可用 (local + jina) |

## 审计

每次 `api_request` 调用的 host / method / credential_name / http_status 都会写入 `ToolResult.details.network`。credential **value** 永远不记录任何地方。

Spec §9 威胁模型列出了 7 类风险及其缓解路径。

## MCP 密文与旧版恢复备份

MCP server override 中的 env/header 映射与其他凭证家族共用
`TIANSHU_SECRET_MASTER_KEY`，持久层只保存密文和 key 名元数据。密钥缺失、错误、格式无效或
密文损坏时，读取和启动 fail closed，不回退到明文或空配置。

v8 明文迁移失败时，数据库旁可保留 **exactly one**
`*.pre-migration-recovery.legacy-sensitive.bak`（**legacy-sensitive recovery backup**）。
文件模式是 **`0600`**，但其中为恢复目的保留了旧明文，`0600` 不能把它降级为
普通备份。运维人员必须执行 **manual protection and cleanup**：

1. 以迁移异常附带的路径为准，立即确认权限仍为 `0600`；
2. 使其远离共享、同步或自动上传目录，并按明文 secret 备份保护；
3. 完成恢复、验证主动库与密文读取后，人工安全删除该文件。

重复失败会复用/替换这一确定路径，不会为每次尝试累积新的
`legacy-sensitive` 备份；成功启动的自动清理不取代上述人工保护/清理流程。详见
[威胁模型](../security/lean-preview-threat-model.md#legacy-plaintext-migration-recovery-warning)。
