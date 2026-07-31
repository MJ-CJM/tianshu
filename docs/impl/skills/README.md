# 技能系统（Skills）— 实现现状

**设计**：[../../design/skills/](../../design/skills/)

## 1. 代码地图

| 路径 | 当前职责 |
|---|---|
| `skills/loader.py` | builtin/user/workspace 加载、缓存、热重载和底层文件原语 |
| `skills/install_service.py` | Skill 候选提案、证据与 gate |
| `skills/installer.py` | 包快照、渲染、路径与内容安全 |
| `skills/guard.py` / `validator.py` | 威胁扫描、信任策略、名称/frontmatter 校验 |
| `skills/metrics.py` | 使用、效果、来源、状态和 Pin |
| `skills/reviewer.py` | 历史复盘逻辑；生产在 LLM 前 fail fast |
| `skills/curator.py` | dry-run 预览和历史整理逻辑；生产写入前 fail fast |
| `tools/skill_tools.py` | 生产注册 `skill_list`、`skill_view`；兼容 manage handler 不公开 |
| `gateway/skills_api.py` | live 目录读取、候选提案/gate/stage、Pin |
| `evolution/promotion.py` | Skill candidate 的 canary、晋升和回滚权威 |

## 2. 生产 Agent 工具

`register_skill_tools(...)` 默认只注册：

| 工具 | tier | 能力 |
|---|---|---|
| `skill_list` | T0_READONLY | 列出可用 Skill，可按名称过滤 |
| `skill_view` | T0_READONLY | 读取全文，并记录本轮使用 |

`skill_manage` 的 handler 为兼容测试保留，但默认不进入 `ToolRegistry`。Prompt 的 Skill
索引也不再提示 Agent 调用必然失败的 create/write_file。

## 3. HTTP 与 Web

- `GET /api/skills`、`GET /api/skills/{name}`：读取当前 live；
- `POST /api/skills`、`PUT /api/skills/{name}`：返回 candidate，不改 live；
- candidate gate / stage：验证并准备，stage 返回 `live_changed=false`；
- delete / archive：治理删除未接通，固定 409；
- Pin：真实更新 metrics，可用；
- Web：一个统一 Skill 目录，显示人工与 Agent 来源，内容只读，仅 Agent Skill 可 Pin。

## 4. 自动后台边界

`AgentConfigState.skill_review_enabled` 和 `skill_curator_enabled` 均默认 `False`。Reviewer
和非 dry-run Curator 还各有独立的 `governed_writes_available` 闸；当前生产装配传
`False`，所以旧持久配置也不会造成无效 LLM 消费。显式 Curator dry-run 仍可预览。

## 5. Loader 与安全原语

Loader 的 `create_skill`、`save_skill`、`patch_skill`、resource write 和 archive/restore
仍是内部原语，并不等于公开 API 能绕过治理。它们使用原子替换并失效缓存；资源路径拒绝
绝对路径、`..`、symlink 逃逸和非白名单顶层目录。候选安装还经过 Validator、Guard、
包快照和证据检查。

## 6. 当前非保证

- 不保证 Web 直接创建或编辑 live Skill；
- 不保证 Agent 自动学习、reviewer 或 curator 会自动激活内容；
- 不保证 delete/archive 已接通治理回滚；
- 不把 candidate created/staged 当作 live activated。
