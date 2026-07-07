# 插件系统实现现状

插件层把第三方扩展接入内核各注册表。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，设计意图见 design 篇。

**相关设计**：[../../design/plugins/README.md](../../design/plugins/README.md)

> 代码位于 `src/tianshu/plugins/`。

## 1. 模块清单（`src/tianshu/plugins/`）

| 文件 | 关键类 / 函数 | 职责 |
|---|---|---|
| `manifest.py` | `PluginManifest`(Pydantic BaseModel) | 插件身份契约：`name` / `version` / `type` / `entry_point` / `dependencies` / `permissions` / `sha256` / `auto_install` |
| `loader.py` | `PluginLoader` | `discover()` 扫目录下每个 `manifest.json` → `PluginManifest`；`load_manifest(name)` 取单个 |
| `api.py` | `PluginApi` | 统一注册门面：`register_plugin` + `register_tool/hook/channel/provider/skill/command` + `list_plugins/get_plugin/list_commands` |
| `installer.py` | `PluginInstaller` | `install_pip(packages)`（subprocess pip）+ `verify_sha256(filepath, expected)` |
| `__init__.py` | 导出 `PluginApi` | 包入口 |

## 2. PluginApi 的委托矩阵

`PluginApi.__init__` 注入五个注册表（均 `Optional`，缺省 `None` → 对应 `register_*` 静默跳过）：

| 注入参数 | 委托方法 → 目标 |
|---|---|
| `tool_registry: ToolRegistry` | `register_tool(name, handler, schema)` → `ToolRegistry.register` |
| `hook_registry: HookRegistry` | `register_hook(hook_type, handler, priority=100)` → `HookRegistry.register` |
| `channel_registry: ChannelRegistry` | `register_channel(channel)` → `ChannelRegistry.register` |
| `provider_manager: ProviderManager` | `register_provider(info)` → `ProviderManager.register` |
| `skills_loader: SkillsLoader` | `register_skill(name, content)` → `SkillsLoader.register_skill`（`hasattr` 守卫） |
| —（自持） | `register_command(name, handler)` → 懒建 `self._commands` dict |

`register_plugin(manifest)` 两处写元数据：进程内 `self._registered_plugins[name] = manifest`，并调 `storage.save_plugin({name, version, manifest=model_dump(), sha256})` 落库。查询走 `list_plugins()` / `get_plugin(name)`（读内存）。

> 注意：`register_tool` 的第三参在门面里命名为 `schema: dict`，但底层 `ToolRegistry.register(name, func, definition)` 期望的是 `ToolDefinition`。注册内建/插件工具时直接传 `ToolDefinition`（见 builtins 写法），治理字段（`tier` / `side_effect` / `max_result_chars`）才会生效。

## 3. PluginLoader.discover 流程

```text
plugins_dir 非目录 → 返回 []
for entry in sorted(plugins_dir.iterdir()):
    entry 非目录 → continue
    entry/manifest.json 不存在 → continue
    try: PluginManifest(**json.loads(read_text)) → append
    except: logger.warning(跳过该插件)
返回成功项列表
```

`sorted` 保证注册顺序确定；任何单清单异常被 `try/except` 兜住，不中断遍历。

## 4. 装配（`app.py` lifespan）

```text
各注册表就绪（tools / hook_registry / channel_registry / provider_manager / skills）
  → PluginApi(storage, tool_registry, hook_registry,
              channel_registry, provider_manager, skills_loader)
  → app.state.plugin_api = plugin_api
  → plugins_dir = <repo_root>/plugins
  → for manifest in PluginLoader(plugins_dir).discover():
        plugin_api.register_plugin(manifest)   # 登记落库
```

`plugins_dir` 解析为仓库根下的 `plugins/`（`Path(__file__).parent.parent.parent / "plugins"`）。当前发现循环只做 `register_plugin` 登记；把清单里的 Tool/Hook/Channel 真正注入注册表，由插件入口（`entry_point`）在自身加载逻辑里调对应 `register_*`，或由二次开发在装配处补一段分派。

## 5. 持久化

`storage.save_plugin(row)` 写插件元数据行（`name` / `version` / `manifest` JSON / `sha256`）。这让重启后可枚举曾注册的插件清单与版本，是 UI/审计的数据源。

## 6. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 让发现循环按 `type` 自动注入能力 | 在 `app.py` 发现循环内，依 `manifest.type` 解析 `entry_point` 取回 handler/channel/info，调对应 `plugin_api.register_*` |
| 安装期校验/装依赖 | 调 `PluginInstaller.verify_sha256(file, manifest.sha256)` 与 `install_pip(manifest.dependencies)`（`auto_install` 为 True 时） |
| 新增可声明的扩展类型 | 扩 `PluginManifest.type` 的 `Literal` 集合，并在 `PluginApi` 加对应 `register_*` 委托到该注册表 |
| 暴露 CLI 插件命令 | 插件调 `register_command(name, handler)`，CLI 层从 `list_commands()` 读回挂载 |

## 7. 端到端写法

写第三方扩展（Tool / MCP / Provider / Plugin / Channel）的最小示例与落点路径见 [../../usage/extension-guide.md](../../usage/extension-guide.md)。
