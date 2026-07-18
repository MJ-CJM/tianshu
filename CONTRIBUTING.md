# 贡献指南

## 先读事实边界

开始前阅读 [Lean Developer Preview 使用指南](docs/usage/lean-developer-preview.md)、
[能力事实矩阵](docs/launch/capability-matrix.md)和
[延期路线图](docs/cc-fable-v1/06-deferred-work-backlog.md)。用户可见术语使用“敕令 / 裁决”；
代码、API 和数据库兼容名保留 `Edict` / `Decree`。

文档和界面必须使用明确的 truth states：`implemented`、`disabled`、`deferred`、
`experimental`、`external_pending`、`user_approval_pending`。设计、计划、截图或局部测试
不能自动把状态提升为已实现或已通过。

## 开发规则

- **TDD：**先写能复现缺口的测试并确认 RED，再做最小实现，最后确认 GREEN 和相关回归。
- **migration freeze：**既有 migration 的 version、名称、checksum 和 callback 不可改写；
  新迁移只允许在当前 ledger 尾部追加，并由对应 brief/测试明确授权。
- **truth-first docs：**能力描述必须链接当前代码、自动化或不可变报告，并同时写清默认值、
  支持范围和非保证。不得把 `external_pending` 写成 passed。
- **no-mock UI：**生产 desktop Web 不得以 mock 数字、复制其他页面数据或静态成功 badge
  兜底；缺失、禁用、延期、权限和错误状态必须真实呈现。
- 只做请求范围内的外科手术式改动；不要顺手清理历史文件、改变移动端或扩大发布面。

## 开发环境

- Python 3.12，Node.js 20。
- 后端：`.venv/bin/python -m pip install -e ".[all,dev]"`。
- 前端：`cd web && npm ci`。
- Ubuntu + Python 3.12 是首个正式目标；其他 OS/Python 组合的本地成功应标为本地证据，
  不能冒充外部矩阵验证。

## 提交前门禁

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/lint-imports
.venv/bin/pytest -m "not slow" -q
cd web && npm run lint && npm run typecheck && npm test -- --run
```

文档改动至少运行对应 truth/link tests 与 `git diff --check`。慢测试使用
`@pytest.mark.slow`；不得用 xfail 掩盖真实回归。

## 贡献与发布权限

欢迎 issue、文档和小修。特性 PR 先开 issue 对齐；单人维护，尽力 48 小时内响应，
不承诺 SLA。`publication_status`: `not_authorized`；贡献权限不包含 push、tag、release、
PyPI/GHCR、仓库公开或外部宣发；这些动作需要维护者另行明确授权。
