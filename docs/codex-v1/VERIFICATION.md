# 验证与证据指南

> **历史验证指南（2026-07-12）：** 以下命令、focused suite 和 Gate 状态绑定当时
> 工作树。当前结果与明确排除项见
> [2026-07-31 本地验证快照](../CURRENT-STATE.md#2026-07-31-本地验证快照)。

## 环境

```bash
env -u VIRTUAL_ENV .venv/bin/python -VV
env -u VIRTUAL_ENV .venv/bin/python -m pytest --version
```

预期项目 Python 为 3.12。不要使用外部 `myenv`；非必要不使用会改写 lockfile 的
`uv run`。

## 每个 Python 切片

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest -q <focused tests>
env -u VIRTUAL_ENV .venv/bin/python -m compileall -q src/tianshu
env -u VIRTUAL_ENV .venv/bin/python -m ruff check <touched files>
env -u VIRTUAL_ENV .venv/bin/python -m ruff format --check <touched files>
env -u VIRTUAL_ENV .venv/bin/python -m mypy
.venv/bin/lint-imports
git diff --check
```

如果模块未在项目 mypy package 清单中，报告“标准 mypy 覆盖范围”与额外定点检查，
不要把历史未覆盖错误伪装成本切片回归。

## G1.4b3 当前核心 focused suite

精确文件列表见 [active S0 core brief](./evidence/active-s0-core-brief.md)。该套件包含真实
Git/文件系统故障注入，历史运行约 8–9 分钟。只启动一个进程并等待完整退出码。

targeted regressions 通过后，才运行 17 文件 suite；最新树尚无完整通过证据。

## Gate 级 Python 回归

```bash
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow"
```

仅主控制者在阶段边界启动一次。必须记录：commit/working-tree identity、命令、退出码、
passed/skipped/deselected/failed、duration 和 warnings。被 Ctrl-C/TERM 终止的运行不算通过。

## 正式 Web

在 `web/`：

```bash
npm test -- --run
npm run typecheck
npm run lint
npm run build
```

G3 增加 Playwright 后还必须运行真实 demo stack journey、axe、键盘、200% zoom、
1280×900/1440×1024 深浅/展开收起视觉矩阵和 console-error Gate。

## G0 原型

在 `prototypes/tianshu-agent-os/`：

```bash
npm test
npm run build
```

原型测试只证明 UI contract 和交互，不证明真实 API、持久裁决或演化。

## 证据状态

| 状态 | 要求 |
|---|---|
| `implemented` | 代码存在，但未取得完整 focused 证据 |
| `focused_verified` | 当前树的命名 focused tests + static checks 完成 |
| `automation_passed` | 当前 commit 的阶段全量 Gate 完成 |
| `external_pending` | 本地/CI 可完成项通过，真实外部项缺失 |
| `user_approval_pending` | 自动/外部候选齐备，等待用户审批 |
| `passed` | 本 Gate 所需证据和授权全部齐备 |

## 禁止的证据替代

- 旧 pytest cache 不能替代新运行；
- 部分进度百分比不能替代退出码；
- fixture/fake 不能替代真实 managed adapter；
- 容器静态校验不能替代 daemon-backed smoke；
- 本机多个目录/VM 不能替代独立外部参与者；
- screenshot 不能替代 API contract 或持久化测试；
- `skip/xfail/not-run` 不能把 required Gate 标绿；
- warnings 不能被“测试通过”一笔带过。
