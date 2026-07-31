# Coding Agent 开发交接指南

> **历史交接快照（2026-07-12）：** 本文已归档，旧分支、暂停点和恢复步骤不适用于
> 当前工作树。当前入口见 [当前实现与支持边界](../../CURRENT-STATE.md)。

## 第一原则

当前工作区包含用户批准、尚未提交的 G1.4b3 安全实现和暂停前的局部 TDD 修复。
不得执行 `git reset --hard`、`git checkout --`、`git clean`、批量格式化或任何会
覆盖 dirty tree 的操作。

## 恢复开发的第一轮检查

在仓库根目录依次执行并保存输出：

```bash
git branch --show-current
git rev-parse HEAD
git status --short --branch
git diff --stat
git diff --check
git ls-files --others --exclude-standard
env -u VIRTUAL_ENV .venv/bin/python -VV
env -u VIRTUAL_ENV .venv/bin/python -m pytest --version
ps -axo pid,ppid,etime,command | rg '[p]ytest|[r]uff|[m]ypy|[l]int-imports'
```

预期 branch 为 `feat_codex_phase_1`；最新提交应为
`docs: add Codex v1 development handoff`，其父提交为业务实现基线 `1b51bcd9`。
若不一致，先重新分析差异，不要自动切换分支。

`docs/codex-v1` 已作为独立 docs commit 跟踪。S0 Commit A/B 不得重复 stage、删除或
改写该目录；“clean tree”指除已知 G1.4b3 owned changes 外不再出现无主改动，并在
S0 A/B 收口后达到真实 `git status` clean。

## 当前 S0 文件所有权

### Commit A：核心 governed-apply 权威

生产文件：

- `src/tianshu/models/workspace.py`
- `src/tianshu/storage/migrations.py`
- `src/tianshu/storage/workspace_repo.py`
- `src/tianshu/executor/anchored_fs.py`
- `src/tianshu/executor/workspace_apply.py`
- `src/tianshu/executor/workspace_service.py`
- `src/tianshu/executor/git_backend.py`

对应测试由 [active S0 core brief](../evidence/active-s0-core-brief.md) 精确定义。

### Commit B：公共 REST/Auth/CLI/capability 表面

生产文件：

- `src/tianshu/app.py`
- `src/tianshu/cli/main.py`
- `src/tianshu/cli/commands/workspace.py`
- `src/tianshu/gateway/auth.py`
- `src/tianshu/gateway/workspace_api.py`
- `src/tianshu/executor/capabilities.py`

测试：CLI、workspace API、Auth、governance preview、executor capability。

不得让两个 agent 同时修改这两个分组，因为 `WorkspaceService`、AuthContext 和
capability truth 存在语义交接。先完成、审查并提交 A，再处理 B。

## S0 恢复步骤

1. 重新冻结 diff；对比 [STATUS.md](./STATUS.md)，记录漂移。
2. 审核暂停前五项局部修复，确认没有半个 edit 或错误测试预期。
3. 先运行以下 targeted regressions：
   - authority 在 validation 期间过期不得被 claim；
   - `close_lease` 等待 source lock 且不留下 pending authority；
   - `after_claim` 同步失败恢复精确快照；
   - post-materialization 非 Git mode 漂移 fail closed；
   - repository apply foundation；
   - frozen V4 callback 拒绝 V5 schema，V5 保持 additive。
4. 运行 active brief 中 17 个 owned test 文件的单一 focused suite。
5. 完成 compileall、Ruff、format、mypy、import-linter 和 diff check。
6. 派发独立只读 reviewer；修复 Critical/Important 并重审。
7. 只 stage Commit A owned files，提交 `feat: add governed workspace apply authority`。
8. 以同样流程完成 Commit B：`feat: expose governed workspace apply surfaces`。
9. 主控制者唯一运行一次完整 `pytest -m "not slow"`，生成 G1.4b3 报告。
10. 工作区干净后才允许进入 S1/G1.5。

## 每个后续切片的工作流

历史计划中的 `REQUIRED SUB-SKILL: superpowers:*` 是原 Codex 环境提示。运行环境有
对应能力时可使用；没有时不构成 blocker，按下面已展开的流程手动完成即可。

1. 从干净 HEAD 和已更新台账开始。
2. 读取 rebaselined plan 和当前阶段单一 brief；不要让实现者读取全部历史文档。
3. 先写最小失败测试并观察预期 RED。
4. 实现最小 GREEN，不做相邻重构或未来扩展。
5. 运行 focused test 和相关静态门禁。
6. 独立 reviewer 同时给出 spec compliance 与 code quality 结论。
7. 修复全部 Critical/Important，重新验证和重审。
8. 提交一个切片并更新 durable ledger。
9. Gate 边界才运行 broad/full suite；禁止多个 agent 重复运行。

切片软上限：生产代码约 800 行、总 diff 约 1,500 行。超过或包含两个独立职责时，
先重拆，不得继续堆积。

## 并行边界

允许并行：

- 只读 recon 与实现；
- 与当前实现无共享文件的文档/测试基础；
- G2 gateway contract 冻结后，最多一个 G3 纯壳层/状态组件切片与一个 G2 后半
  独立切片；二者必须有具名 owner、文件集合不相交，integration owner 仍唯一。

禁止并行：

- 两个实现者同时改 dirty tree；
- 并发修改 migration、`app.py`、CLI 注册或公共契约；
- 重复启动 broad/full pytest；
- G2 migration 与未冻结的 G1 migration 同时实施；
- 用 G3 mock UI 先定义后端真值。

## 环境规则

- Python 命令使用 `env -u VIRTUAL_ENV .venv/bin/python`。
- 不使用外部 `/Users/chenjiamin/myenv`。
- 验证时避免 `uv run` 自动改写 `uv.lock`；依赖确需同步时先明确记录。
- source quality 与 repo hygiene 分开报告；不要用根目录历史 demo 噪声掩盖源码结果。
- warnings 必须记录，尤其是 coroutine、ResourceWarning、数据库/子进程泄漏。

## 阶段入口

- S1/G1.5：完整 S0 通过、migration v5 冻结、工作区干净。
- S2/G1.6：G1.5 exact wheel/hash/manifest 和 readiness 冻结。
- G2：完整 G1 handoff、migration prefix、SystemAudit/MCP/Workspace contracts 冻结。
- G3 真实页：对应 G2 Decision/RunState/Evidence/readiness contract tests 已通过。
- G4：G2 Gate 和 G3 automation green；真实外部项可保持 `external_pending`，但不能标 G4 passed。
- G5：G4 公共协议冻结；实际发布等待用户最终授权。
