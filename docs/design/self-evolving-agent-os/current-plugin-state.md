# 当前插件扩展实现与支持边界

> **Status: Current source fact。**
> 本页描述当前 Tianshu 插件能力；目标态设计见
> [target-architecture.md](target-architecture.md)。

当前 `plugins` 能力是 metadata-only catalog：它只发现、校验和登记本地
`manifest.json`，不安装依赖、不 import `entry_point`，也不执行第三方插件代码。因此它是
实验性的插件清单，不是动态 PluginHost。

本页合并原 `docs/design/plugins/` 与 `docs/impl/plugins/` 的内容，作为本报告目录中的唯一
插件现状说明。用户开发示例仍在 [扩展开发指南](../../usage/extension-guide.md)，但当前/目标
能力边界以本目录为准。

## 1. 当前支持矩阵

| 能力 | 状态 | 当前行为 |
|---|---|---|
| manifest 发现 | 可用 | `PluginLoader` 按目录顺序读取 `plugins/<name>/manifest.json` |
| manifest 校验 | 有限 | `PluginManifest` 校验 JSON 形状和 `type` 枚举 |
| 元数据登记 | 可用 | 名称、版本、原始清单和声明的 SHA-256 写入 SQLite |
| Web/API 查询 | 可用 | 只显示 `manifest_only`，明确 `loaded=false` |
| 动态安装 | 不支持 | `POST /api/plugins/install` 返回 `501 plugin_install_not_supported` |
| 激活/停用 | 不支持 | `PUT /api/plugins/{name}/status` 返回 `501 plugin_activation_not_supported` |
| entry point 加载 | 不支持 | 启动过程不会 import 或调用 `entry_point` |
| 依赖与指纹验证 | 不支持 | `dependencies`、`sha256`、`permissions`、`auto_install` 只是声明字段 |

单个清单解析失败只记录 WARNING 并跳过，不影响主服务启动。这里的 fail-soft 仅适用于无副
作用的元数据发现；代码加载继续 fail closed。

## 2. 当前实现

代码位于 [`src/tianshu/plugins/`](../../../src/tianshu/plugins/)：

| 文件 | 当前职责 |
|---|---|
| [`manifest.py`](../../../src/tianshu/plugins/manifest.py) | `PluginManifest` 数据模型；entry point、依赖、权限和 SHA-256 均为声明字段 |
| [`loader.py`](../../../src/tianshu/plugins/loader.py) | `discover()` / `load_manifest()` 只读取并解析 JSON |
| [`api.py`](../../../src/tianshu/plugins/api.py) | 登记 manifest 元数据；为受信任源码装配提供显式 `register_*` 门面 |

仓库目前不存在 `PluginInstaller`，也没有通用 import、pip 安装、SHA-256 验证、依赖解析、
卸载或隔离执行链。

启动装配如下：

```text
各内建注册表就绪
  → 创建 PluginApi
  → PluginLoader(settings.plugins_dir).discover()
  → 对每个合法 manifest 调 register_plugin()
  → SQLite 记录 status=manifest_only
  → 结束；不解析或执行 entry_point
```

`plugins_dir` 来自 `TianshuSettings.plugins_dir`，支持 `~` 展开。发现顺序使用 `sorted`
保持确定。

## 3. API 与 Web

| 路由 | 行为 |
|---|---|
| `GET /api/plugins` | 返回清单目录，强制 `status=manifest_only`、`loaded=false` |
| `GET /api/plugins/{name}` | 返回单条清单目录记录 |
| `POST /api/plugins/install` | `501 plugin_install_not_supported` |
| `PUT /api/plugins/{name}/status` | `501 plugin_activation_not_supported` |

Web 只展示“仅清单”和发现时间，不把数据库中的历史 `active` 值解释成代码已经加载。对应
边界由 [`test_plugin_manifest_api.py`](../../../tests/gateway/test_plugin_manifest_api.py)
锁定。

## 4. 受信任源码扩展

`PluginApi.register_tool/hook/channel/provider/skill/command` 可以把已经由内建代码实例化的
对象交给相应注册表。这是程序化扩展门面，不是 manifest 自动加载路径。

显式使用这些门面的调用者仍需在源码和部署流程中承担：

- 导入、实例化和版本兼容；
- Policy、Decision 和凭据边界；
- 失败、关闭和测试责任。

现有注册表并非全部没有卸载原语：`HookRegistry.unregister` 与
`ProviderManager.unregister` 已存在。真实缺口是 `ToolRegistry`、`ChannelRegistry` 和
`ExecutorAdapterRegistry` 没有对应的安全 `unregister`，同时 `PluginApi` 没有跨注册表的
contribution owner、统一 disposer、generation、依赖闭包与单插件逆序卸载语义。因此仍不能
把这些 `register_*` 方法描述成目标态 PluginHost。

## 5. P1 典制影子归因

P1 在不打开动态插件加载的前提下，新增了 `SystemSnapshotV1`（典制）的内容身份和影子绑定。
它解决的是“这次运行实际看到了哪些系统内容”，不是“如何安装或热替换第三方插件”。

| 组件键 | P1 的实际内容投影 |
|---|---|
| `kernel` | 天枢版本与共享 dependency-lock 标记 |
| `executor:<adapter_id>` | 当前注册表中每个执行器 manifest 的内容摘要 |
| `skills` | 各搜索层的 `SKILL.md`、`scripts/references/assets/templates` 资源及受信任源码注入的 Skill 内容 |
| `personas` | 当前在编 Persona 的语义字段及其 runtime `SOUL.md` / `ROLE.md` 内容 |
| `policy_rules` | 内建规则 id 与 priority 的确定性投影 |
| `provider_profiles` | 内建 Profile 与持久 Provider 的无明文密钥、无时间戳语义投影 |
| `evolution_overlay` | 仅 governed assignment 存在；legacy assignment 明确省略 |
| `prompts` | schema 已预留，本阶段不填 |

V31 `0031_system_snapshots` 用不可更新、不可删除、不可 replace 的内容寻址表保存典制，
`run_system_bindings` 按 `(memorial_id, attempt_id)` insert-once 记录运行事实。成功绑定会在
Evidence 中增加 `application/vnd.tianshu.system-snapshot.v1+json` required artifact；assignment
只读 API 和 Edict 详情可以投影同一内容摘要。

这一阶段仍是 **shadow / 影子模式**：默认开关只控制是否写入，解析或持久化失败会尽力写入
SystemAudit 与 durable outbox，但不改变任务结果；`system_snapshot_strict` 只登记配置，严格
拒绝语义留到 P6。它也不等于完整运行内容已经全部可归因：dependency-lock 目前仍是零值占位，
policy 只覆盖 id/priority，prompt key 尚未填充；更没有 RuntimeGeneration、动态加载、第三方
依赖闭包、Canary 切换或热卸载。

## 6. 为什么当前不开自动加载

执行第三方入口前至少需要完成：

- 可安装来源、依赖锁定、内容寻址、签名和 provenance；
- API/ABI、Host 版本和状态 schema 协商；
- entry point 生命周期、owner/disposer、健康检查和卸载语义；
- Tool、Hook、Channel、Provider、Skill、Command 的 Capability 与冲突规则；
- 文件、网络、Secret 和资源配额；
- generation 并存、warming、Canary、last-good 和回滚。

这些边界完成前，“发现了清单”不能展示为“插件已安装或激活”。从当前能力到目标态的迁移
顺序见 [migration-roadmap.md](migration-roadmap.md)。
