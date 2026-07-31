# 插件清单实现现状

> 代码位于 `src/tianshu/plugins/`。当前实现是 metadata-only catalog，不是运行时插件
> loader。产品边界见 [设计说明](../../design/plugins/README.md)。

## 模块

| 文件 | 当前职责 |
|---|---|
| `manifest.py` | `PluginManifest` 数据模型；`entry_point`、依赖、权限和 SHA-256 均为声明字段 |
| `loader.py` | `discover()` / `load_manifest()` 只读取并解析 JSON |
| `api.py` | 登记 manifest 元数据；为受信任源码装配提供显式 `register_*` 门面 |

仓库不存在 `PluginInstaller`，也没有通用的 import、pip 安装、SHA-256 校验、卸载或隔离
执行链。

## 启动装配

```text
各内建注册表就绪
  -> 创建 PluginApi
  -> PluginLoader(settings.plugins_dir).discover()
  -> 对每个合法 manifest 调 register_plugin()
  -> SQLite 记录 status=manifest_only
  -> 结束；不解析或执行 entry_point
```

`plugins_dir` 来自 `TianshuSettings.plugins_dir`，支持 `~` 展开。单个损坏清单被跳过并写
WARNING；发现顺序使用 `sorted` 保持确定。

## API 与 Web

| 路由 | 行为 |
|---|---|
| `GET /api/plugins` | 返回清单目录，强制 `status=manifest_only`、`loaded=false` |
| `GET /api/plugins/{name}` | 返回单条清单目录记录 |
| `POST /api/plugins/install` | `501 plugin_install_not_supported` |
| `PUT /api/plugins/{name}/status` | `501 plugin_activation_not_supported` |

Web 只展示“仅清单”和发现时间，不再把数据库中的历史 `active` 值解释成代码已加载。

## 受信任源码接入

`PluginApi.register_tool/hook/channel/provider/skill/command` 可以把已经由内建代码实例化的
对象交给相应注册表。这是程序化扩展点，不是 manifest 自动加载路径。若二次开发显式使用，
其测试必须覆盖相应 Policy、Decision、凭据、失败和关闭语义。
