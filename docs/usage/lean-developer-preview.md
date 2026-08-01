# Lean Developer Preview 本地安装与黄金 Demo

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

## 1. 定位

本文是 v0.4.2 批次（`20260719T083725Z-01da3844dde7`）wheel 的严格复验流程，面向想按
哈希链（source commit → Wheel SHA-256 → 证据报告）复现验证的读者。历史阶段档案见
[docs/cc-fable-v1/](../cc-fable-v1/)，能力现状见
[能力事实矩阵](../launch/capability-matrix.md)。新用户请从
[getting-started.md](getting-started.md) 开始。

## 2. 源码安装 / Source checkout

在 Ubuntu + Python 3.12、Node.js 20 的本地 checkout 中：

```bash
python3.12 -m venv .source-venv
.source-venv/bin/python -m pip install -e .
cd web
npm ci
npm run build
cd ..
TIANSHU_STARTUP_PROFILE=demo .source-venv/bin/python -m uvicorn \
  tianshu.app:create_app --factory --host 127.0.0.1 --port 7998
```

该路径用于本地开发。新的 Candidate 若要成立，仍须使用下面的 exact Wheel
fresh-install 路径；本轮没有执行该 Gate。

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
  "tianshu @ file://$WHEEL"
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
[`test_fresh_wheel_demo.py`](../../tests/packaging/test_fresh_wheel_demo.py)
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

- Batch：`20260719T083725Z-01da3844dde7`
- Source：`01da3844dde77b5a9e56f346bed9b2605f7bc832`
- Wheel SHA-256：
  `bb1c0ca64cc125713863dfe4a927b5f8bc35ec0ff06a7d25b73ad3e121521f76`
- Report：
  [`demo-report.json`](../cc-fable-v1/evidence/lean-preview/20260719T083725Z-01da3844dde7/demo-report.json)
- Closure report：
  [`closure-task-3-report.md`](../../.superpowers/sdd/closure-task-3-report.md)

该批次 `fixture=false`，通过全部 13 步、严格 verifier、loopback-only descendant profile、
清洁 SIGTERM、SQLite `quick_check=ok` 与 package resource digest 不变。它是本地
Darwin/arm64/Python 3.12.12 证据。OpenHands、ROI、cost calibration 和 full G4 为
`external_pending`；full G5 为 `deferred`。该批次也不是 Ubuntu 外部矩阵或正式发布证据。
该批次为历史保留证据（retained evidence）；后续批次的证据须绑定各自的源码
Gate、build provenance 与 demo，历史批次不跨批复用。

## 8. 桌面品牌事实

生产 desktop Web 使用 [`web/public/brand.png`](../../web/public/brand.png)，SHA-256 为
`3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`；格言为
“成功只有一个——按照自己的方式，去度过人生。”；右上五项为
“彩蛋 / 通用 / English / 实时 / 通政”。默认侧栏为“中枢 / 御书房 / 朝堂 / 百司 /
天工院〔实验〕 / 内府”六个一级入口：御书房包含全部敕令、颁发敕令、钦天监、都察院；朝堂
包含吏部、廷议、内阁；百司包含翰林院、鸿胪寺、通政司；天工院包含演化司〔实验〕、
诸界台〔实验〕、考功司〔试行〕、客卿馆〔实验〕；内府保留藏兵阁、权印司、户部账房。
御书房统一承载当前主体可见且未归档的全部任务、真实进度和待人工介入事项。任务类型
标签允许叠加，定时、长程和实验性的客卿任务仍可发现；旧 `/edicts` 地址兼容跳转到
御书房。深浅主题和收起控制保留。

客卿馆的安装状态不是静态清单：后端每次请求都会读取本机 CLI 安装版本，页面进入时、
停留期间每 15 秒以及窗口重新聚焦时都会同步。页面同时展示“已安装版本”和“已验证
基线”；天枢不会自动执行 Pi 升级，检测到新版本时先提示待兼容验证，契约与离线 RPC
检查通过后才更新验证基线。当前本地验证的 Pi 基线为 `0.83.0`。

“中枢总览”现展示四张独特能力卡：长程治理、自进化、平行位面、客卿。自进化卡使用
后端真实 `evolution_status` 投影（`not_enabled / enabled / degraded`），不再永久显示
固定状态。当前非视觉 Web 自动化已覆盖首次引导、首个任务直达详情、六入口导航、天工院
四页可达性、真实错误/404、键盘与 200% 缩放。

保留的 48 张视觉基线和哈希覆盖前一版 6 路由产品壳，包含 Universes、Evals、Keqing
的双视口、双主题和侧栏展开/收起组合。最新源码已将御书房加入矩阵，定义 7 路由、
预期 56 张图片；本轮已完成隔离网页功能点验，但视觉图片和哈希尚未重新生成——
视觉终审属于已知待办，功能点验不等于视觉终审已通过。
