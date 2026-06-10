# 插件系统（plugins）

插件是「吏部」：第三方包通过一份 `manifest.json` 声明身份，经统一门面 `PluginApi` 把 Tool / Hook / Channel / Provider / Skill / Command 注册进各自已有的注册表，无需触碰内核装配代码。

**相关实现**：[../../impl/plugins/README.md](../../impl/plugins/README.md)

## 1. 为什么这样设计

天枢已经为每类扩展点准备了独立注册表（`ToolRegistry` / `HookRegistry` / `ChannelRegistry` / `ProviderManager` / `SkillsLoader`）。插件层的目标**不是再造一套注册机制**，而是：

- **门面而非框架**：`PluginApi` 只做「把声明转交给对应注册表」的薄委托，不持有插件运行时、不发明新的生命周期。这样插件能力与内核能力天然等价——插件注册的工具和内建工具走同一条 `PolicyEngine` / 审批治理链，不存在「插件特权通道」。
- **声明先行**：`manifest.json` 是插件的身份契约（名称、版本、类型、权限、SHA-256）。发现阶段只解析清单、落库元数据，**不执行插件代码**，把「我是谁」和「我做什么」分成两步，便于审计与按需加载。
- **降级容错**：单个清单解析失败只记 WARNING 跳过，不影响其余插件与主启动——发现循环对损坏/恶意清单 fail-soft。

## 2. 三个核心构件

| 构件 | 文件 | 职责 |
|---|---|---|
| `PluginManifest` | `plugins/manifest.py` | 插件身份契约（Pydantic 模型，字段缺省即合法） |
| `PluginLoader` | `plugins/loader.py` | 扫描插件目录 → 每个子目录的 `manifest.json` → `PluginManifest` 列表 |
| `PluginApi` | `plugins/api.py` | 统一注册门面，按类型委托给对应注册表 + 持久化元数据 |

辅助件 `PluginInstaller`（`plugins/installer.py`）负责 `pip install` 依赖与 SHA-256 校验，是可选的安装期工具，不参与注册主链。

## 3. PluginManifest — 身份契约

字段全部带缺省值，最小可用清单只需 `name`：

| 字段 | 语义 |
|---|---|
| `name` | 插件唯一标识（注册表与持久化的主键） |
| `version` / `description` / `author` / `homepage` | 元信息 |
| `type` | `tool` / `hook` / `channel` / `provider` / `skill` / `command` 之一，声明插件主能力 |
| `entry_point` | 插件入口（模块/可调用引用），由安装/加载侧约定解释 |
| `dependencies` | pip 依赖列表，交 `PluginInstaller.install_pip` |
| `permissions` | 声明式权限清单（审计用） |
| `sha256` | 包指纹，交 `PluginInstaller.verify_sha256` 校验 |
| `auto_install` | 是否允许自动安装依赖 |

设计取舍：`type` 是 `Literal` 枚举而非自由字符串——发现阶段就能对类型做静态约束；其余字段宽松缺省，降低写清单的门槛。

## 4. PluginLoader — 发现而不执行

`discover()` 的契约：

```text
若 plugins_dir 不是目录 → 返回空列表（无插件目录不是错误）
遍历 plugins_dir 下每个子目录（sorted，顺序确定）:
    若无 manifest.json → 跳过
    json.loads → PluginManifest(**data)
    解析失败 → logger.warning 跳过该插件，继续下一个
返回成功解析的 PluginManifest 列表
```

关键点：**发现阶段只读清单，不 import 插件代码**。约定一个插件占一个子目录、清单固定名为 `manifest.json`，使「目录即插件」无歧义；`sorted` 保证多插件注册顺序可复现。`load_manifest(name)` 是按名取单个清单的旁路，供精确加载。

## 5. PluginApi — 统一注册门面

`PluginApi` 构造时注入各注册表（全部 `Optional`，缺省 `None`）。这让插件门面在**部分注册表未装配**时仍能工作——对应 `register_*` 静默 no-op，便于裁剪部署与测试。

注册方法与委托目标：

| 方法 | 委托至 | 行为 |
|---|---|---|
| `register_plugin(manifest)` | `Storage.save_plugin` | 记录到内存 `_registered_plugins` + 落库（name/version/manifest/sha256） |
| `register_tool(name, handler, schema)` | `ToolRegistry.register` | 注册工具 |
| `register_hook(hook_type, handler, priority=100)` | `HookRegistry.register` | 注册生命周期 hook |
| `register_channel(channel)` | `ChannelRegistry.register` | 注册通知渠道 |
| `register_provider(info)` | `ProviderManager.register` | 注册 LLM provider |
| `register_skill(name, content)` | `SkillsLoader.register_skill` | 注册技能（loader 支持时） |
| `register_command(name, handler)` | 自身 `_commands` | 暂存插件 CLI 命令 |

`register_plugin` 同时维护**两份元数据**：进程内 `_registered_plugins`（`list_plugins` / `get_plugin` 查询）与持久化行（重启后可枚举曾装过的插件）。其余 `register_*` 是「能力注入」——把插件的工具/渠道等并入内核注册表后，它们与内建能力在运行时不可区分。

## 6. 装配位置

`app.py` lifespan 在各注册表就绪后构造 `PluginApi`，随即 `PluginLoader(plugins_dir).discover()` 并对每个清单调 `register_plugin` 落库登记。注册表的注入顺序（Tool/Hook/Channel/Provider/Skills 都已存在）决定了插件门面拿到的是**完整能力面**。详见实现篇的装配小节。

## 7. 安全边界

- 治理无特权通道：插件工具经 `ToolRegistry` 注册后，同样受 `PolicyEngine` tier 判定与审批拦截，见 [../tools/policy.md](../tools/policy.md)。
- `permissions` / `sha256` 是声明式审计字段——清单自带指纹，`PluginInstaller.verify_sha256` 在安装期比对，发现期不强制。
- 发现 fail-soft：损坏清单被跳过而非中断启动，避免单个坏插件拖垮整机。
