# 贡献指南

## 开发环境

- Python ≥ 3.12,Node ≥ 20
- 后端:`uv sync --extra all --extra dev`(或 `pip install -e ".[all,dev]"`)
- 前端:`cd web && npm install`
- 启用钩子:`uv run pre-commit install`

## 质量门禁(提交前本地全绿)

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/lint-imports          # 分层架构契约
.venv/bin/pytest -m "not slow" -q
cd web && npm run lint && npm run typecheck && npm test -- --run
```

## 分支与提交

- `main` 常绿;特性走短分支(`feat/<topic>`,1–3 周内经 PR 合入)
- 提交信息用 conventional commits:`feat:` `fix:` `refactor:` `docs:` `test:` `chore:` `perf:` `ci:`
- 新增代码测试覆盖尽量 ≥ 90%;仓库整体覆盖率目标 80%(现状 63%,每迭代递增)
- 术语以根目录 [CONTEXT.md](CONTEXT.md) 为准;架构决策见 [docs/adr/](docs/adr/)

## 测试

- 慢测试(真实子进程)标记 `@pytest.mark.slow`,CI 默认排除
- 偶发抖动测试标记 `@pytest.mark.flaky` 并附根修 TODO,禁止 xfail 掩盖

## 贡献策略(首发期,详见 [docs/adr/0005](docs/adr/0005-narrow-gate-contribution.md))

- 欢迎 issue、讨论、文档改进与小修 PR(typo/bugfix)
- **特性 PR 请先开 issue 对齐**——项目处于滚动排期高速迭代期,未经对齐的大 PR 可能被建议拆分或归档
- issue 尽力 48 小时内响应;单人维护,不作 SLA 承诺
- 行为准则采用 [Contributor Covenant](https://www.contributor-covenant.org/zh-cn/version/2/1/code_of_conduct/)
