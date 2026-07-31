# 插件清单（实验）

当前 `plugins` 能力只负责发现和登记本地 `manifest.json`，不安装依赖、不 import
`entry_point`，也不执行第三方插件代码。它是一个实验性的清单目录，不是可直接使用的
动态插件系统。

**相关实现**：[../../impl/plugins/README.md](../../impl/plugins/README.md)

## 当前支持

| 能力 | 状态 | 行为 |
|---|---|---|
| manifest 发现 | 可用 | `PluginLoader` 按目录顺序读取 `plugins/<name>/manifest.json` |
| manifest 校验 | 有限 | 用 `PluginManifest` 校验 JSON 形状和 `type` 枚举 |
| 元数据登记 | 可用 | 名称、版本、原始清单和声明的 SHA-256 写入 SQLite |
| Web/API 查询 | 可用 | 只显示 `manifest_only`，明确 `loaded=false` |
| 动态安装 | 不支持 | `POST /api/plugins/install` 返回 `501 plugin_install_not_supported` |
| 激活/停用 | 不支持 | `PUT /api/plugins/{name}/status` 返回 `501 plugin_activation_not_supported` |
| entry point 加载 | 不支持 | 启动过程不会 import 或调用 `entry_point` |
| 依赖与指纹验证 | 不支持 | `dependencies`、`sha256`、`auto_install` 目前只是声明字段 |

单个清单解析失败只记录 WARNING 并跳过，不影响主服务启动。这里的 fail-soft 仅适用于
无副作用的元数据发现；代码加载保持 fail-closed。

## 为什么不开自动加载

自动执行第三方入口还需要先定义并验证：

- 可安装来源、依赖锁定、签名或内容寻址；
- entry point 生命周期、版本兼容和卸载语义；
- 权限模型、凭据隔离、网络与文件系统边界；
- Tool、Hook、Channel、Provider、Skill、Command 各自的治理适配；
- 加载失败、升级失败和回滚的持久状态。

这些边界尚未完成前，把“发现了清单”展示成“插件已激活”会误导用户，也会扩大供应链
风险。因此开源预览只保留清单目录。

## 源码级扩展

`PluginApi` 仍提供 `register_tool`、`register_hook`、`register_channel`、
`register_provider`、`register_skill` 和 `register_command`，供受信任的内建装配或二次
开发显式调用。调用者必须在源码和部署流程中自行承担导入、实例化、版本与安全审查；
manifest 不会自动触发这些方法。

若未来开放动态插件，应单独经过设计与安全审批，不能把当前声明字段直接升级为执行权限。
