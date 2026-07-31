# 运行与部署边界

> 当前状态入口：[`../CURRENT-STATE.md`](../CURRENT-STATE.md)。本页只说明怎么安全运行当前
> 代码，不宣称已经发布官方制品。

## 1. 当前支持矩阵

| 路径 | 当前结论 | 边界 |
|---|---|---|
| 源码 checkout | 支持的开发/验证路径 | 以当前依赖锁和本地配置运行 |
| 当前源码构建的 Wheel | 可构建、可做本地制品检查 | 未发布 PyPI；最近一次检查未覆盖 Ubuntu 全新 HOME exact-Wheel 安装 |
| `Dockerfile` 本地镜像 | 可本地构建/试运行 | 非官方镜像，未发布 GHCR，不带签名/SBOM/provenance 承诺 |
| PyPI/GHCR/Release | 未发布 | 需要单独发布授权和 Gate |
| 多节点/K8s/PostgreSQL | 不支持 | single-node SQLite，无多写者/replica failover 保证 |

## 2. Dockerfile 做了什么

当前 Dockerfile 是三阶段本地验证镜像：

1. Node builder 用 lockfile 构建 Web static；
2. Python builder 把同一 Web payload 打入 Wheel；
3. `python:3.12-slim-bookworm` runtime 安装 Wheel，以 UID/GID `10001:10001` 运行。

运行数据、memory、persona、plugin、logs 和 workspace 路径通过 `/data`、`/workspace`
显式配置；healthcheck 使用 `/health/live`。这能证明镜像结构的非 root/local smoke
边界，不证明基础镜像长期补丁、依赖全锁定、生产容量或公网加固。

使用 `scripts/docker.sh build/start/status`。trusted-local 默认只允许发布到 loopback；
脚本解析 Docker bridge 的精确 gateway，并设置 container boundary。不要手工把
trusted-local 容器映射到 `0.0.0.0`。

## 3. trusted-local

适合用户控制的单机环境。宿主默认 bind loopback；容器模式只能通过脚本建立的精确私网
gateway 例外。trusted-local 允许明确请求的 host fallback，因此不是强 sandbox，也不抵抗
宿主管理员、恶意 root、数据库/trust-root 替换或本地 secret 读取。

## 4. secure-remote

公网/远程访问必须使用 `TIANSHU_SECURITY_MODE=secure-remote`，并完成配置模型要求的：

- HTTPS `public_base_url`；
- 精确 `allowed_hosts` 和 HTTPS `allowed_origins`；
- 受限 trusted proxy CIDR；
- session/PAT 与 cookie/CSRF 边界；
- 不使用 trusted-local container gateway 例外。

secure-remote 只证明这些 admission/auth 边界；它不会自动把外部进程变成强 sandbox。
ExecutionGateway 在需要 sandbox 而没有受支持后端时 fail closed，不回退宿主。

## 5. MCP

| 组合 | 当前行为 |
|---|---|
| disabled server | 不启动 |
| trusted-local stdio + 显式非空 `tools.include` | 可准入；仍非 OS sandbox |
| stdio + 空 include | 拒绝 `approved_tools_required` |
| secure-remote streamable_http | 拒绝 `trusted_egress_unavailable` |
| remote MCP 公网开放 | 延期，不支持 |

当前 stdio admission 没有持久绑定 executable digest/realpath、argv、env、cwd、actor、
reason、expiry 或 discovered-tool drift。配置字段存在不等于这些安全保证已经实现。

## 6. 备份与升级

- 干净停机后备份主 DB、WAL/SHM 状态和 memory/persona 文件；
- 启动 migration 前不要让多个实例同时打开同一 DB，虽然 startup lock 会串行迁移，也不
  构成多实例运行支持；
- 敏感 migration 失败可能留下 `legacy-sensitive` 恢复备份，按
  [credentials.md](credentials.md) 保护并清理；
- 升级后检查 `/health/ready`，不要只看 `/health/live`。
