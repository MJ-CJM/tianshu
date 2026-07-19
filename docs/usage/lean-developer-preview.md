# Lean Developer Preview 本地安装与黄金 Demo

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

本指南覆盖当前两条正式本地安装路径：**源码安装（Source checkout）**与同一 checkout
构建的 **exact Wheel**。Ubuntu + Python 3.12 是首个正式支持目标；最终保留批次实际在
本地验证环境 `Darwin/arm64/Python 3.12.12` 完成，因此不能把该批次当作 Ubuntu 外部
复验证据。产品面为 **desktop Web only**；mobile 产品为 `deferred`。

`publication_status`: `not_authorized`。本指南不授权公开仓库、push、tag、release、
PyPI/GHCR、官方容器或对外宣发。

## 1. 支持与信任边界

- 运行模型：single-host、single-node SQLite；host administrator 可访问数据库、主密钥、
  进程内明文、工作区和本地产物，不在当前防护对象内。
- `implemented`：SystemAudit、MCP persisted secret ciphertext、durable governance、
  Evidence Bundle v1、三张核心 desktop Web 页面自动化、Lean Core evolution 黄金路径。
- remote MCP：`disabled`；open stdio MCP：`disabled`。两者的 Candidate 正式开放面关闭。
- `experimental`：Keqing 与尚未冻结完整支持契约的 Lean evolution 扩展面。
- `deferred`：official container、PyPI、GHCR、签名、完整 provenance、mobile 与十四部门
  全部深度产品化。
- `external_pending`：Ubuntu 外部复验、VoiceOver、OpenHands、executor compatibility、
  ROI、cost calibration 和 full G4。
- `deferred`：full G5；它不进入当前 Candidate 的外部复验队列。
- `user_approval_pending`：desktop Web 视觉与交互终审。

逐项证据见[能力事实矩阵](../launch/capability-matrix.md)，安全细节见
[SECURITY.md](../../SECURITY.md)，恢复条件见
[延期路线图](../cc-fable-v1/06-deferred-work-backlog.md)。

## 2. 源码安装 / Source checkout

在 Ubuntu + Python 3.12、Node.js 20 的本地 checkout 中：

```bash
python3.12 -m venv .source-venv
.source-venv/bin/python -m pip install -e ".[cli]"
cd web
npm ci
npm run build
cd ..
TIANSHU_STARTUP_PROFILE=demo .source-venv/bin/python -m uvicorn \
  tianshu.app:create_app --factory --host 127.0.0.1 --port 7998
```

该路径用于本地开发。正式 Candidate 证据使用下面的 exact Wheel fresh-install 路径。

## 3. 构建并安装 exact Wheel

从待验证 commit 构建；目录中必须只有一个本次 Wheel：

```bash
python3.12 -m venv .build-venv
.build-venv/bin/python -m pip install "build==1.5.0"
cd web
npm ci
npm run build
cd ..
.build-venv/bin/python -m build --wheel --outdir dist/lean-preview
WHEEL_COUNT="$(find "$PWD/dist/lean-preview" -maxdepth 1 -name 'tianshu-*.whl' | wc -l | tr -d ' ')"
test "$WHEEL_COUNT" -eq 1
WHEEL="$(find "$PWD/dist/lean-preview" -maxdepth 1 -name 'tianshu-*.whl' -print -quit)"
test -n "$WHEEL"
SOURCE_COMMIT="$(git rev-parse HEAD)"
WHEEL_SHA256="$(shasum -a 256 "$WHEEL" | awk '{print $1}')"
python3.12 -m venv .preview-venv
.preview-venv/bin/python -m pip install --only-binary=:all: \
  "tianshu[cli] @ file://$WHEEL"
```

不要把同名 registry package、legacy Dockerfile 或另一个 commit 的 Wheel 混入此验证。

## 4. 启动 fresh HOME 服务

以下环境与已提交 black-box harness 使用相同 public contract。工作区先初始化为空 Git
仓库；服务只监听 loopback。`ENVIRONMENT_FINGERPRINT` 必须由调用者按当前环境事实计算，
不能复制历史批次值。

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/tianshu-preview.XXXXXX")"
DEMO_HOME="$DEMO_ROOT/fresh HOME 用户"
DEMO_WORKSPACE="$DEMO_ROOT/workspace 工作区"
mkdir -p "$DEMO_HOME" "$DEMO_WORKSPACE"
git -C "$DEMO_WORKSPACE" init -q
git -C "$DEMO_WORKSPACE" config user.email golden-demo@example.invalid
git -C "$DEMO_WORKSPACE" config user.name "Lean Preview"
git -C "$DEMO_WORKSPACE" commit --allow-empty -q -m "golden demo baseline"

TOKEN="replace-with-a-local-random-token"
TOKEN_HASH="$(printf '%s' "$TOKEN" | shasum -a 256 | awk '{print $1}')"
ENVIRONMENT_FINGERPRINT="$({
  .preview-venv/bin/python -c 'import json,platform,sys; sys.stdout.buffer.write(json.dumps({"architecture":platform.machine() or "unknown","dependency_lock_hash":"0"*64,"platform":platform.system() or "unknown","python_version":platform.python_version(),"tianshu_version":"0.4.2","workspace_base_revision":None},ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(",",":")).encode("utf-8"))'
} | shasum -a 256 | awk '{print $1}')"

export HOME="$DEMO_HOME"
export PYTHONNOUSERSITE=1
export TIANSHU_STARTUP_PROFILE=demo
export TIANSHU_DB_PATH="$DEMO_HOME/.tianshu/tianshu.db"
export TIANSHU_ARTIFACT_DIR="$DEMO_HOME/.tianshu/artifacts"
export TIANSHU_MEMORY_DIR="$DEMO_HOME/.tianshu/memory"
export TIANSHU_RUNTIME_PERSONAS_DIR="$DEMO_HOME/.tianshu/personas"
export TIANSHU_LOG_DIR="$DEMO_HOME/.tianshu/logs"
export TIANSHU_PLUGINS_DIR="$DEMO_HOME/.tianshu/plugins"
export TIANSHU_WORKSPACE_DIR="$DEMO_WORKSPACE"
export TIANSHU_WORKSPACE_STAGING_ROOT="$DEMO_HOME/.tianshu/workspaces"
export TIANSHU_UNIVERSE_REPO_ROOT="$DEMO_WORKSPACE"
export TIANSHU_TELEMETRY=off
export TIANSHU_AUTH_BOOTSTRAP_TOKEN_HASH="sha256:$TOKEN_HASH"
export TIANSHU_BOOTSTRAP_TOKEN="$TOKEN"
export TIANSHU_EVOLUTION_ROUTING_SECRET="replace-with-a-local-routing-secret"
export TIANSHU_LEAN_SOURCE_COMMIT="$SOURCE_COMMIT"
export TIANSHU_LEAN_WHEEL_SHA256="$WHEEL_SHA256"
export TIANSHU_LEAN_ENVIRONMENT_FINGERPRINT="$ENVIRONMENT_FINGERPRINT"
export TIANSHU_LEAN_FIXTURE=false

.preview-venv/bin/python -m uvicorn tianshu.app:create_app --factory \
  --host 127.0.0.1 --port 7998 >"$DEMO_ROOT/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill -TERM "$SERVER_PID" 2>/dev/null || true' EXIT
until curl --fail --silent http://127.0.0.1:7998/health/ready >/dev/null; do sleep 1; done
```

完整的 descendant-process 禁网、外部安装路径、清洁 SIGTERM、SQLite quick-check 与资源
digest 证明由
[`test_lean_preview_fresh_wheel.py`](../../tests/launch/test_lean_preview_fresh_wheel.py)
执行；手工命令不能替代该 retained Gate。

## 5. 单一黄金 Demo 命令

只有这一处定义公开黄金 runner 入口。安装后的 console script 通过 loopback public API
执行固定 13 步；失败批次保留并使用新 batch ID 重跑。

```bash
BATCH_ID="$(date -u +%Y%m%dT%H%M%SZ)-${SOURCE_COMMIT:0:12}"
.preview-venv/bin/tianshu-lean-demo \
  --base-url http://127.0.0.1:7998 \
  --scenario "$PWD/examples/lean-governed-evolution/scenario.json" \
  --batch-id "$BATCH_ID" \
  --output-root "$PWD/docs/cc-fable-v1/evidence/lean-preview"
```

## 6. 严格 verifier 与 provenance

verifier 必须同时收到调用者独立测得的 source commit 和 Wheel SHA；仅验证报告内部 hash
不足以接受批次。

```bash
.preview-venv/bin/python scripts/verify_lean_preview_evidence.py \
  --report "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/demo-report.json" \
  --artifact-root "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/artifacts" \
  --expected-source-commit "$SOURCE_COMMIT" \
  --expected-wheel-sha256 "$WHEEL_SHA256"
```

接受条件：13 步全部 `passed`；report/artifact/schema/hash、Evidence、candidate/gate、真实
overlay、rollback receipt、source commit 和 Wheel SHA 全部一致。任何失败或缺失都返回
非零；不得手改历史批次。

## 7. 历史已验证保留批次

- Batch：`20260718T072917Z-b27f525fe4ef`
- Source：`b27f525fe4eff52a24f0c7769125bc158097e7de`
- Wheel SHA-256：
  `81ec17b9818e67ac6046fb0e1ab62d13606fcaa5af14141ae4d311179bc10fef`
- Report：
  [`demo-report.json`](../cc-fable-v1/evidence/lean-preview/20260718T072917Z-b27f525fe4ef/demo-report.json)
- Closure report：
  [`closure-task-3-report.md`](../../.superpowers/sdd/closure-task-3-report.md)

该批次 `fixture=false`，通过全部 13 步、严格 verifier、loopback-only descendant profile、
清洁 SIGTERM、SQLite `quick_check=ok` 与 package resource digest 不变。它是本地
Darwin/arm64/Python 3.12.12 证据。OpenHands、ROI、cost calibration 和 full G4 为
`external_pending`；full G5 为 `deferred`。该批次也不是 Ubuntu 外部矩阵或正式发布证据。
旧 Candidate 聚合产物已撤销，因此该批次仅作历史 retained evidence，不得复用为新
Candidate；新 Candidate 必须绑定新的 final-source Gate、build provenance 与 demo。

## 8. 桌面品牌事实

生产 desktop Web 使用 [`web/public/brand.png`](../../web/public/brand.png)，SHA-256 为
`3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`；格言为
“成功只有一个——按照自己的方式，去度过人生。”；右上五项为
“彩蛋 / 通用 / English / 实时 / 通政”。四组十四部门导航、深浅主题和收起控制保留；
Candidate 的深度产品承诺只覆盖中枢总览、敕令详情和演化中心。
