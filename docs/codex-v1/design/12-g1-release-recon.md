# G1 Phase 1.5–1.6 发布准备勘察

> 勘察日期：2026-07-11
> 范围：Fresh install、doctor、mock provider、package resources、Docker、CI/release、SECURITY/threat model、MCP secrets/remote/stdio。
> 性质：只读代码勘察；除本文档外未修改实现、工作流、数据库或运行环境。
> 上游基线：`docs/superpowers/plans/2026-07-10-open-source-agent-os-master-roadmap.md` 的 1.5–1.6，以及 `docs/superpowers/specs/2026-07-10-open-source-agent-os-optimization-design.md`。
> 边界：本文不重新设计 1.1 身份、1.2 Capability Manifest、1.3 ExecutionGateway 或 1.4 WorkspaceService，只标明 1.5–1.6 对它们的依赖和集成点。

## 1. 结论先行

当前仓库**尚不满足 G1 Developer Preview 的发行条件**。源码检出后的开发体验已有基础，但 wheel、容器和 MCP 安全边界仍存在发布级阻断。

### P0 阻断项

1. **wheel 不是自包含产品**：`personas/`、`templates/persona/` 位于包外；已生成的 `src/tianshu.egg-info/SOURCES.txt` 也未包含 builtin skills、Web static、persona/template 和 executor Markdown templates。多个 bootstrap 模块仍按源码仓根目录反推资源路径。
2. **fresh DB 没有默认 persona**：当前只播种六个 department，`tests/gateway/test_personas_api.py` 明确断言全新库 `/api/personas` 为空；默认执行者 `bingbu`、规划者 `neige`、助手 `tongzheng` 因而无法在 fresh install 直接使用。
3. **无离线 demo provider**：运行路径最终仍进入 LiteLLM；无 key 时 `tianshu doctor` 直接 fail，无法在不接入真实 LLM 的情况下证明第一条敕令闭环。
4. **doctor 不足以充当发行自检**：只有 Rich 表格，没有稳定 JSON schema；未检查 package resources、readiness、MCP、真实沙箱能力、迁移兼容状态或发行 profile；API key 输出仍包含前缀片段。
5. **Docker 不是最小、锁定、非 root 运行时**：runtime 使用 root，保留 `build-essential`、`wget`、`jq`、`zip/unzip` 等工具；Python 依赖通过浮动的 `pip install ".[cli]"` 解析；镜像只装 `cli` extra；base image 使用可漂移 tag；healthcheck 只访问固定返回 ok 的 `/health`。
6. **CI 没有发行证据**：现有 `ci.yml` 只做源码树 backend/frontend gate，`uv sync` 未带 `--frozen`；没有 wheel 外部安装、fresh HOME、container smoke、dependency/code scan、SBOM 或 release workflow。
7. **liveness/readiness 未分离**：`/health` 无条件返回 `{"status":"ok"}`，DB、scheduler、mandatory sandbox 不可用时仍为 200。
8. **MCP secrets 仍明文落库**：`mcp_server_overrides.env_json`、`headers_json` 保存完整值，API 虽只回传 key 名，但数据库本身无加密。
9. **remote MCP 无发布级网络边界**：`streamable_http` 只校验 URL 存在；未做 scheme/userinfo/SSRF/host allowlist/DNS rebinding 判定。MCP SDK 默认 `follow_redirects=True`，可能把 Authorization header 带到跳转目标。
10. **stdio MCP 可启动任意命令**：UI/API 可提交任意 `command + args`，DB-only server 默认 enabled；当前 allowlist 只限制 server 名，不限制 executable、argv、cwd 或 capability grant，也没有配置变更后撤销 grant 的语义。
11. **MCP 拒绝缺少持久系统审计**：当前主要是普通日志；G2 的通用 `SystemAuditEvent` 尚未落地，而 1.6 验收要求负向用例被拒绝并形成系统审计记录。

### 可复用基础

- G0 已有 versioned migration ledger、升级前在线备份、离线 restore 和 fail-closed schema 检查，可直接承载后续 persona seed 与 MCP secret migration。
- 已有 `SecretVault`/Fernet、主密钥轮换、credential encrypted storage，可复用加密原语和运维约束。
- MCP 已有配置模型、YAML/DB merge、server-name allowlist、tool include/exclude、tier、错误脱敏和 mock transport 测试。
- Web 构建产物已位于 `src/tianshu/web/static/`，只缺 package-data 与默认定位契约。
- 基础 CI 已覆盖 Ruff、format、mypy、import-linter、pytest、ESLint、typecheck、Vitest 和 Web build。
- Docker 已是 frontend/backend 两阶段，继续收敛为 wheel-builder + runtime 即可，无需重写部署模型。

## 2. 现状证据与具体缺口

| 范围 | 当前证据 | 发布缺口 |
|---|---|---|
| Python metadata | `pyproject.toml` 只有 packages.find；没有 package-data、readme、SPDX license、authors/classifiers/project URLs；console script 永远安装，但 Typer/Rich 只在 `cli` extra | 裸 wheel 的 CLI 契约不完整，资源与发行元数据不足 |
| wheel manifest | `src/tianshu.egg-info/SOURCES.txt`（2026-07-11 11:17）不含 `src/tianshu/skills/builtin/**`、`src/tianshu/web/static/**`、根 `personas/**`、根 `templates/persona/**`、executor `*.md` | 现有 editable/source 测试不能证明 wheel 可用 |
| persona paths | `wiring_persona.py`、`wiring_memory.py`、`wiring_llm.py`、`memory/manager.py` 用 `Path(__file__).../personas` 反推 repo root | site-packages 下会指到不存在目录 |
| default persona | `_seed_departments()` 只写 department；`test_list_personas_empty_by_default` 断言空列表 | fresh install 无 `bingbu/neige/tongzheng`，主流程无法直接演示 |
| builtin skills | `wiring_skills.py` 从 package 内 `skills/builtin` 读取，目录结构合理 | 非 Python `SKILL.md` 未显式纳入 wheel |
| Web static | Vite outDir 是 `src/tianshu/web/static`；`TianshuSettings.static_dir` 默认 `/app/static` | wheel 外运行不会自动发现包内 Web；静态文件也未显式进入 wheel |
| prompt templates | `executor/orchestrator/templates.py` 读取 `executor/templates/edict/*.md` | wheel manifest 未包含这些文件，只会静默 fallback，掩盖发行缺件 |
| mock provider | tests 中存在大量 fake/mock，但生产 provider 只有 LiteLLM 路径 | 无真实 key 时不能黑盒运行一条任务；测试 fake 不是产品能力 |
| doctor | 检查 key、DB 目录、workspace、端口、secret key、Feishu/Telegram import，`--llm` 才发真实请求 | 缺稳定机器输出、mock、资源、schema/readiness、MCP、sandbox、完整 extras；默认把无 key 判为 fail |
| Docker | `ubuntu:24.04` runtime，root；安装 build tools 和多个通用 CLI；只 `pip install ".[cli]"`；COPY source；固定 `/app/static` | 非最小、非锁定、不是 wheel 发行物、MCP extra 不在镜像、无法证明资源完整 |
| health | `app.py` 的 `/health` 始终 200；Docker/local/sandbox/deployer 都把它当健康 | liveness 与可接单状态混为一谈 |
| CI | 只有 `.github/workflows/ci.yml`；`uv sync --extra all --extra dev` 未 frozen | lock 可漂移；无 artifact/smoke/security/SBOM/release gate |
| MCP persistence | `schema.py` 中 `env_json TEXT`、`headers_json TEXT`；`config_repo.py` 直接 `json.dumps` | DB/备份均可读到旧明文；缺 migration 与 key-missing fail-closed |
| MCP API | GET 只返回 `env_keys/header_keys`，这是正确方向；POST/PATCH 接收完整值 | 写入后仍为明文，PATCH 也无 secret replace/clear 的明确契约 |
| MCP remote | `transport.py` 直接调用 SDK；SDK factory 默认 follow redirects | 无 SSRF/DNS/redirect/header boundary |
| MCP stdio | `StdioServerParameters(command,args,env)` 直接 spawn；SDK 默认环境本身是安全子集 | 天枢侧仍无 executable/argv/cwd grant，`npx` 可通过 argv 执行任意包 |
| MCP defaults | `MCPServerConfig.enabled=True`、create body `enabled=True`、tools include 空表示全开；无 allowlist 时全允 | 对新建外部执行面默认过宽 |
| Security docs | `SECURITY.md` 有披露邮箱、Fernet、急停和诚实边界 | 缺 supported versions、运行模式、threat model、MCP/容器/供应链边界与 SBOM 说明 |

## 3. 依赖与执行顺序

1.5–1.6 不应作为一个大提交落地。建议按以下顺序，每一步都能独立回滚和验收：

1. **R1 Package resources + metadata**：先让 wheel 内容完整，建立唯一资源定位 API。
2. **R2 Default persona seed**：在 versioned migration 内只对全空 persona 表播种一次。
3. **R3 Explicit demo/mock provider**：让所有 LLMClient 构造路径在 mock model 下都不触网。
4. **R4 Structured doctor**：基于前 3 项给出可机器消费的安装判定。
5. **R5 Fresh wheel smoke**：从 repo 外 cwd、fresh HOME、空 DB 验证 Web/persona/skill/mock Edict。
6. **R6 Liveness/readiness**：把容器与发布 smoke 的健康语义先钉死。
7. **R7 Non-root locked container**：容器安装已验证 wheel，而不是再次复制源码形成第二套发行路径。
8. **R8 MCP encrypted persistence migration**：在网络/stdio 放开前先消灭主动库中的明文值。
9. **R9 MCP admission policy**：remote 和 stdio 分别 fail closed，并消费 1.1 AuthContext、1.2 capability、1.3 ExecutionGateway。
10. **R10 Security/release workflows + docs**：最后把上述证据固化到 CI、SBOM、SECURITY 和 threat model。

### 必须等待的上游接口

- **1.1**：`RuntimeMode`（`trusted-local / secure-remote`）、`Principal/AuthContext`、Host/Origin/TLS proxy policy。1.6 不另造第二套“是否生产”开关。
- **1.2**：`ExecutorCapabilityManifest`。MCP capability grant 应使用同一能力词汇，不创建冲突枚举。
- **1.3**：`ExecutionGateway`。stdio MCP 的 spawn 必须通过统一命令边界；`transport.py` 不应继续直接成为治理外 subprocess 入口。
- **1.4**：WorkspaceService。如 MCP grant 包含 cwd/path scope，必须使用 canonical workspace/staging ID，而不是裸字符串前缀判断。

## 4. Phase 1.5 最小可落地方案

### 4.1 Package resources 与 wheel metadata

建立一个唯一资源入口 `tianshu.resources`，业务模块不再推导 repo root：

- 把根 `personas/` 移入 `src/tianshu/resources/personas/`。
- 把根 `templates/persona/` 移入 `src/tianshu/resources/persona_templates/`，保留 `SOURCES.md` 与上游 commit/license 信息。
- 保留 `src/tianshu/skills/builtin/` 和 `src/tianshu/web/static/` 的现有位置，但在 package-data 中显式列出。
- 把 `src/tianshu/executor/templates/edict/*.md` 显式列入 package-data，禁止依赖 fallback 掩盖 wheel 缺件。
- `tianshu.resources` 暴露 `personas_dir()`、`persona_templates_dir()`、`builtin_skills_dir()`、`web_static_dir()`；正常 pip wheel 安装必须返回 site-packages 内稳定 Path。zipimport 不作为 G1 支持形态。
- `static_dir` 未显式配置时使用 packaged `web_static_dir()`；容器仍可用 env 覆盖，但不再是 wheel 的必需条件。
- 在 `pyproject.toml` 明确 MIT license、README、作者显示名（不凭空加入邮箱）、Python/OS classifiers、project URLs、package-data 和 supported extra。
- 解决 console script 与 `cli` extra 的冲突：G1 推荐把 Typer/Rich/CLI 运行必需项放入 base，新增 `server` extra 承载 MCP server 等服务端可选项；`all` 显式包含 server + channels。不要发布一个安装后 `tianshu --help` 立即 ImportError 的 base wheel。
- 当前 `fcntl` migration lock 使 Windows 不是已验证平台。G1 metadata 只声明 Linux/macOS；如要声明 Windows，必须先引入可移植锁并加入 Windows fresh-install matrix。

### 4.2 默认 persona seed

- 建立 canonical seed 定义，固定当前六个内建 ID：`bingbu`、`ducha`、`hubu`、`neige`、`tongzheng`、`wenyuan`；`court` 是共享上下文，不写成可执行 persona。
- 使用 migration ledger 的“下一个可用版本”做数据 migration：仅当 `personas` 表为空时，在单事务内插入六条；已有任意用户 persona 的库不自动混入默认角色。
- migration 一次成功后由 ledger 防止“用户主动删除全部 persona 后被下次启动复活”。
- seed 的 SOUL/ROLE 路径不写 repo 绝对路径；`PersonaLoader` 始终从 packaged department template materialize 到 `runtime_personas_dir`。
- 对六条 seed 做 schema 校验，至少保证默认执行者 `bingbu`、默认规划者 `neige`、默认助手 `tongzheng` 可加载。

### 4.3 明确的 demo/mock provider

- 新增显式 startup profile：`standard` 与 `demo`。`demo` 不是 LLM 失败后的 fallback，必须由用户/env 主动选择，并在 API/doctor/日志中标记。
- demo profile 使用 `mock/tianshu-demo` 模型；不要求 API key、不联网、零计费、输出可重复。
- mock backend 同时实现 `chat()` 与 `chat_stream()`，返回合法 `LLMResponse/UsageSummary`；按 prompt 类型产生最小合法响应（普通执行结果、planner JSON、completion audit JSON、rubric pass），不依赖全局调用顺序。
- 由于 Planner、Auditor、Memory 等模块仍存在直接 `LLMClient(...)`，mock 识别必须位于统一 `LLMClient` backend dispatch，而不能只放在 `ProviderManager.get_client()`。
- 任何 mock 结果都带 `actual_model=mock/tianshu-demo`、`upstream_provider=mock` 和 demo marker；不能混入真实评测、成本或演化晋升样本。
- G1 demo 默认只用于 trusted-local onboarding；secure-remote 不自动启用 demo，也不允许 live provider 失败后静默降级到 mock。

### 4.4 Structured doctor

将诊断逻辑从 CLI 渲染中拆出，形成稳定 schema：

```text
DoctorReport(schema_version, overall, mode, checks[])
DoctorCheck(id, category, level, summary, remediation, evidence)
```

- `--format table|json`；JSON stdout 不混 Rich markup，退出码 `0=无 fail`、`1=存在 fail`、`2=doctor 自身配置/调用错误`。
- 默认只做离线、低副作用检查；真实 provider 与 container probe 分别通过显式参数开启。
- 检查分类：runtime/auth config、live/mock provider、DB path + read-only quick/schema state、workspace writable/boundary、port + liveness/readiness、package resources、default persona、builtin skills、Web static、configured extras、MCP master key/policy、sandbox capability、Git/runtime executables。
- demo mode 无真实 key 是 ok；standard live mode 无 key 是 fail。输出只写 `configured/source`，不再显示 key 前六位。
- existing DB 的 schema 检查使用 read-only 连接；doctor 不应通过启动 Storage 偷跑 migration。
- sandbox 检查区分“CLI 存在”“daemon 可用”“满足 mandatory capability”；secure-remote 需要的 sandbox 不可用时 fail，未启用的可选 Universe sandbox 只 warn。

### 4.5 Fresh wheel black-box smoke

发行 smoke 必须满足：

- wheel 在临时 venv 安装；`cwd` 位于 repo 外；`HOME`、DB、workspace 全新；清空 `PYTHONPATH`。
- `tianshu doctor --format json` 在 demo profile 返回 0。
- wheel manifest 包含六组 persona 资源、396 份 persona template + `SOURCES.md`、2 个 builtin skills、Web index/brand/assets、3 个 executor edict templates。
- 空 DB 启动后 `/api/personas` 至少包含六个 seed，skills API 能看到 `file-ops` 与 `shell`，GET `/` 与 `brand.png` 为 200。
- 提交一条显式指派 `bingbu` 的 mock Edict，轮询到 terminal，断言 memorial/result/model marker；同时断言 LiteLLM HTTP 函数未被调用。
- smoke 全程不需要网络、真实 LLM key、源码目录或已有 `~/.tianshu`。

## 5. Docker 最小方案

### 5.1 镜像结构

使用三阶段：frontend builder → Python wheel/dependency builder → runtime。

- 所有 base image 在实现时锁定到审核过的 digest；本文不写易过期的 digest 值。
- frontend 继续 `npm ci` + `npm run build`。
- Python builder 使用 `uv.lock` 的 frozen export/sync；build backend 也必须进入锁定的 build group，不能让 `setuptools>=68` 在发行时重新浮动解析。
- builder 产出与 wheel smoke 相同的 wheel；runtime 只安装该 wheel 的 `server` profile，不复制源码树。
- runtime 使用 Python slim 基础，创建固定 non-root UID/GID；预建并授权 `/data`、`/workspace`、`/home/tianshu`。
- 删除 Node、npm、compiler、headers、wget、jq、zip/unzip 等构建/通用工具。保留的 `git`、CA 或其它工具必须有实际 runtime consumer，并进入 capability matrix/doctor。
- `HEALTHCHECK` 使用 Python stdlib 请求 `/health/ready`，不为一个 healthcheck 保留 curl。
- image labels 写 version、revision、source、license；容器的安全模式、端口发布和 volume 权限写入 docs。
- `.dockerignore` 明确排除 `.tianshu/`、DB/WAL/SHM、migration backups、`.env*`（保留 example 的显式例外）、tests/prototypes/audit artifacts 和本地 venv。当前 `tianshu/.tianshu/` 模式不能替代根 `.tianshu/`。

### 5.2 容器 smoke

- 构建后检查实际 UID 非 0。
- `/data`、`/workspace` 可写，其余 app/runtime 层只读；可增加 `--read-only` + tmpfs `/tmp` smoke。
- 镜像中不存在 `node/npm/gcc/make`；对保留的 `git` 做显式断言。
- 以 fresh volume + demo provider 启动，验证 doctor JSON、liveness、readiness、Web、default persona、builtin skills 和 mock Edict。
- 宿主端口只 publish 到 `127.0.0.1`。如容器内部必须监听 `0.0.0.0`，则按 1.1 定义的 container profile 启动；secure-remote smoke 必须携带测试身份，不能绕过 auth。

## 6. Phase 1.6 最小可落地方案

### 6.1 Liveness / readiness

- 保留 `/health` 作为兼容 alias，但明确它等同 liveness，不作为“系统可接单”证据。
- 新增 `/health/live`：仅证明进程/event loop 可响应，不读取外部依赖。
- 新增 `/health/ready`：结构化返回每个 check；必要 check 失败返回 503。
- readiness 必查：Storage 可查询且 migration ledger 无 pending/failed、scheduler running、关键后台 worker 已启动。
- sandbox 仅在 effective Governance Contract 声明 mandatory 时进入必要项；optional MCP/channel/telemetry 故障不应让整个核心服务永久 unready。
- Docker、release smoke、部署晋升/回滚使用 readiness；UI 的系统状态必须区分 live 与 ready。

### 6.2 MCP secret persistence

G1 最小方案采用“整张 env/header mapping 加密”，避免在此阶段引入通用 secret-reference 平台：

- 为 `mcp_server_overrides` 增加 `env_ciphertext BLOB`、`headers_ciphertext BLOB` 和非敏感 key-name metadata；重建表后移除 active schema 中的明文 `env_json/headers_json`。
- API 仍可一次提交完整 mapping，但 service 在同一事务前完成 Fernet encryption；GET 永远只返回 key names/`configured=true`，PATCH 用 replace/clear 的显式字段，不能用 COALESCE 模糊“未传”和“清空”。
- versioned migration 读取 legacy plaintext、加密写新表、回读解密比对、再 drop legacy table；全程不把 value 放入 exception、log、event 或 ledger。
- 若存在非空 legacy plaintext 而 `TIANSHU_SECRET_MASTER_KEY` 缺失/无效，migration fail closed，保留原库和升级前 backup；空 secret maps 可无 key 迁移。
- 升级前 backup 会忠实包含旧明文，这是历史数据的安全副本而不是“已加密 active DB”。它必须保持 0600、在文档中标记为敏感并进入备份保留/销毁指引；不能宣称备份也自动无明文。
- 抽取底层 secret codec 到低层 security/secrets 边界，避免 migration 反向依赖高层 CredentialStore；同步更新 import-linter contract。
- YAML 推荐只存 env reference；未解析的 `${VAR}` 在 enabled server 上必须 fail closed，而不是把字面量交给子进程。literal non-secret env 与 secret env 的文档语义要明确。

### 6.3 Remote MCP 网络准入

- secure-remote 下 remote MCP 默认 disabled；启用必须同时满足 server allowlist、exact host allowlist、HTTPS、无 userinfo、允许端口、有效 TLS 和可证明的 egress boundary。
- 在 config admission 和每次 reconnect 前解析全部 A/AAAA；任一 loopback/private/link-local/multicast/reserved/metadata/CGNAT/ULA 地址即拒绝，混合 public+private 也拒绝。
- 通过 MCP SDK 的 `httpx_client_factory` 注入受控 client，G1 直接禁用 redirects；要求配置最终 URL，避免跨 origin header 泄漏。
- 现有 `hongluisi.validate_url()` 可以复用地址分类，但它是“校验后再次解析”的 TOCTOU 模式，不能单独宣称解决 DNS rebinding。
- **最小可证明策略**：secure-remote 若没有 connect-time IP pin 或受信 egress proxy/firewall，则 remote MCP 保持 disabled；trusted-local 可提供 preflight-only experimental 模式，并在 capability matrix 明示没有硬 DNS-rebinding 保证。
- 所有拒绝只记录 URL origin/host、policy code 和解析后的安全分类，不记录 path query/header value。

### 6.4 stdio MCP command/capability grant

- 新建 DB/UI server 默认 `enabled=false`，tools include 默认空且语义改为“未授予任何工具”，不是全开。
- stdio config 必须绑定一份带版本的 grant：server、resolved executable、argv fingerprint、cwd/workspace scope、env key names、network policy、允许的 discovered tool names/tier、actor、reason、expiry。
- executable 使用 realpath 后匹配 exact allowlist；拒绝 shell wrapper、相对路径逃逸、可写目录中的可替换二进制。仅允许 `npx` 名称没有意义，必须把 package+version+argv 一并纳入 fingerprint。
- config 中 command/args/env/cwd/tool set 任一变化都会使旧 grant 失效并自动 disable，等待重新裁决。
- spawn 由 1.3 `ExecutionGateway` 执行；transport adapter 只消费已批准 request，不自行 `subprocess`。
- MCP server 返回的 tool 描述不作为安全事实；tool capability/tier 以 grant 为上限，未知或新出现工具默认不注册。
- trusted-local 也要求显式 grant，只是 actor 可为 local operator；secure-remote 必须来自 AuthContext 且有权限。

### 6.5 安全审计的跨 phase 收口

1.6 的“拒绝并记录系统审计”不能只靠普通日志。推荐在 G1 先落一个窄的 append-only `system_audit_events` 最小模型，G2 在兼容 migration 上扩展，而不是 G1 写日志、G2 再换语义：

- 字段至少包含 `id/schema_version/action/resource/outcome/policy_code/actor/correlation_id/source_ip/reason/details_json/created_at`。
- G1 只写 auth、MCP secret/config、remote admission、stdio grant/deny、readiness override 等安全动作。
- details 先经结构化 allowlist + redaction；表无 update/delete 业务 API。
- 如主计划坚持把持久 SystemAuditEvent 全部留到 G2，则 1.6 验收文字必须降为“结构化安全日志”，不能在 G1 声称已有持久系统审计。二者必须在实施前二选一；推荐前者。

## 7. CI、SBOM 与 release workflow

### 7.1 `ci.yml`

- 所有 `uv sync/export` 使用 `--frozen`；CI 结束断言 `git diff --exit-code -- uv.lock`。
- 保留现有 backend/frontend gate；coverage floor 只在重新确认当前基线后冻结，不沿用旧数字猜阈值。
- GitHub Actions 与第三方 action 在发行分支 pin commit SHA，并由 Dependabot/Renovate 提 PR 更新。

### 7.2 `release-smoke.yml`

- Linux + macOS、Python 3.12 基线；新增版本只有在显式支持后进入 matrix。Windows 在 portable migration lock 前不宣称支持。
- build wheel/sdist → inspect contents → repo 外 fresh venv/HOME/cwd → doctor → start → Web/persona/skill/mock Edict。
- Linux job 额外运行 container non-root smoke。
- smoke 不触发 PyPI/GHCR 发布，仅上传日志、manifest、wheel 和 image digest 证据。

### 7.3 `security.yml`

- Python dependency scan 使用 frozen lock/export；Web 使用 package-lock。扫描工具版本固定。
- code scan 覆盖 Python 与 TypeScript；结果以 SARIF 或构建 artifact 输出。
- `uv export --frozen --format cyclonedx1.5` 生成 Python SBOM，`npm sbom` 生成 Web SBOM；如要求 container OS SBOM，再引入 pin 版本的 Syft/同类工具。
- vulnerability DB/registry 暂时不可达时不得伪装 pass；区分“clean / findings / scanner unavailable”，发行 gate 对 unavailable fail closed。
- secret-history scan、provenance/attestation 和正式制品签名仍按 Master Roadmap G5 收口；G1 不提前宣称完成。

### 7.4 `release.yml`

- `workflow_dispatch` 和受保护 tag/environment 触发；构建只消费已经通过的 smoke/security jobs。
- 校验 README/pyproject/Web/version 一致、`twine check`、artifact SHA256、SBOM 附件。
- G1 只产生 Developer Preview artifacts；不默认自动发布 PyPI/GHCR。PyPI Trusted Publishing、GitHub environment approval 和仓库权限必须由维护者在外部平台配置后才能开启 publish job。

## 8. 文件清单

### 8.1 Phase 1.5

**Create**

- `src/tianshu/resources/__init__.py`
- `src/tianshu/resources/default_personas.py`
- `src/tianshu/resources/personas/**`
- `src/tianshu/resources/persona_templates/**`
- `src/tianshu/providers/mock_provider.py`
- `src/tianshu/diagnostics/__init__.py`
- `src/tianshu/diagnostics/models.py`
- `src/tianshu/diagnostics/checks.py`
- `src/tianshu/diagnostics/runner.py`
- `tests/resources/test_package_resources.py`
- `tests/providers/test_mock_provider.py`
- `tests/integration/test_fresh_install.py`
- `scripts/release_smoke.py`
- `.github/workflows/release-smoke.yml`

**Move/remove source duplicates**

- `personas/**` → `src/tianshu/resources/personas/**`
- `templates/persona/**` → `src/tianshu/resources/persona_templates/**`

**Modify**

- `pyproject.toml`
- `src/tianshu/config.py`
- `src/tianshu/llm.py`
- `src/tianshu/providers/manager.py`
- `src/tianshu/bootstrap/wiring_persona.py`
- `src/tianshu/bootstrap/wiring_memory.py`
- `src/tianshu/bootstrap/wiring_llm.py`
- `src/tianshu/bootstrap/wiring_skills.py`
- `src/tianshu/memory/manager.py`
- `src/tianshu/persona/loader.py`
- `src/tianshu/persona/template_library.py`
- `src/tianshu/web.py`
- `src/tianshu/storage/migrations.py`
- `src/tianshu/cli/commands/doctor.py`
- `tests/cli/test_doctor.py`
- persona/memory/template tests that currently point at repo-root `personas/` or `templates/`
- `README.md`
- `README.en.md`
- `docs/usage/getting-started.md`
- `.env.example`

### 8.2 Docker / health / release

**Create**

- `src/tianshu/gateway/health.py`
- `tests/gateway/test_health_readiness.py`
- `tests/integration/test_container_smoke.py`
- `.github/workflows/security.yml`
- `.github/workflows/release.yml`

**Modify**

- `Dockerfile`
- `.dockerignore`
- `src/tianshu/app.py`
- `src/tianshu/scheduler/scheduler.py`（只增加稳定 readiness state，不暴露内部 task）
- `src/tianshu/universe/sandbox.py`
- `src/tianshu/universe/deployer.py`
- `scripts/local.sh`
- `web/src/api/health.ts`
- `.github/workflows/ci.yml`
- `docs/launch/checklist.md`

### 8.3 MCP / security

**Create**

- `src/tianshu/secrets/mcp_store.py`
- `src/tianshu/security/mcp_remote_policy.py`
- `src/tianshu/security/mcp_stdio_policy.py`
- `src/tianshu/security/system_audit.py`（若采用推荐的 G1 持久审计）
- `src/tianshu/storage/system_audit_repo.py`（同上）
- `tests/secrets/test_mcp_secret_migration.py`
- `tests/security/test_mcp_remote_policy.py`
- `tests/security/test_mcp_stdio_policy.py`
- `tests/security/test_system_audit.py`（同上）
- `docs/ops/threat-model.md`

**Modify**

- `src/tianshu/storage/schema.py`
- `src/tianshu/storage/migrations.py`
- `src/tianshu/storage/config_repo.py`
- `src/tianshu/storage/__init__.py`
- `src/tianshu/tools/mcp/config.py`
- `src/tianshu/tools/mcp/manager.py`
- `src/tianshu/tools/mcp/transport.py`
- `src/tianshu/tools/mcp/client.py`
- `src/tianshu/gateway/mcp_api.py`
- `tests/tools/mcp/test_config.py`
- `tests/tools/mcp/test_allowlist.py`
- `tests/tools/mcp/test_http_transport.py`
- `tests/tools/mcp/test_manager.py`
- `tests/tools/mcp/test_redact.py`
- `SECURITY.md`
- `docs/ops/mcp_servers.yaml.example`
- `docs/design/tools/mcp.md`
- `docs/launch/capability-matrix.md`
- `pyproject.toml` 的 import-linter/security dependencies

## 9. 测试与烟测矩阵

| 层级 | 场景 | 必须断言 | 外部依赖 |
|---|---|---|---|
| Unit | resource locator | 所有 canonical resource 存在，路径不落 repo root | 无 |
| Unit | wheel content | wheel zip 含 persona/template/skills/static/executor templates/license | build backend |
| Unit | persona seed | 空表一次播种 6 条；非空不混入；重启/删除不复活 | SQLite |
| Unit | mock provider | chat/stream 合法、确定性、零成本、有 marker、LiteLLM 未调用 | 无网络 |
| Unit | doctor models | table/json 同语义；无 key 在 demo ok、live fail；无 secret prefix | 无 |
| Unit | doctor DB/resources | read-only schema check、missing resource fail、sandbox capability 分级 | fake FS/runtime |
| Integration | fresh wheel | repo 外 cwd + fresh HOME + empty DB；doctor/Web/persona/skill pass | 临时 venv |
| Integration | mock Edict | 指派 bingbu 后 terminal；memorial/result/model marker 正确 | 本地 loopback |
| Unit | readiness | DB/scheduler/mandatory sandbox 任一失败时 503；optional MCP 失败不拖死核心 | fakes |
| Container | non-root | UID 非 0；data/workspace 可写；app 只读；无 Node/compiler | Docker daemon |
| Container | fresh smoke | ready/Web/default resources/mock Edict；restart 后数据保留 | Docker daemon |
| Migration | MCP plaintext → encrypted | active DB 查不到 sentinel；解密等值；ledger/backup 正确 | Fernet key |
| Migration negative | key 缺失/错误/中断 | fail closed、源库不变、错误/日志无 sentinel、backup 0600 | SQLite |
| Unit | MCP remote URL | http/file/userinfo/localhost/private/link-local/metadata/IPv6 ULA/mixed DNS 全拒 | fake resolver |
| Unit | DNS rebinding | public→private 解析变化拒绝；secure-remote 无硬 egress boundary 时 disabled | fake resolver |
| Unit | redirects | 3xx 不自动跟随；Authorization 不发往第二 origin | fake HTTP transport |
| Unit | stdio grant | 默认 disabled；无 grant/argv 变化/symlink/shell wrapper/npx 未锁版本全部拒绝 | fake gateway |
| Integration | allowed stdio fixture | exact grant 启动 fixture，发现工具；config 改动立即失效 | Python fixture |
| Audit | negative paths | 每次拒绝有 actor/correlation/policy_code，无 secret/path query | SQLite/log capture |
| CI | dependency/code scan | findings 阻断；scanner unavailable 不是 pass | vuln DB/CodeQL |
| CI | SBOM | Python + Web SBOM 可解析且包含 tianshu/version | uv/npm |
| Release dry run | artifact graph | wheel/image/SBOM/hash 来自同一 commit/version | GitHub Actions |

### 建议 gate 命令（实施完成后）

```bash
uv sync --frozen --extra server --extra dev
uv run pytest tests/resources tests/providers tests/cli/test_doctor.py -q
uv run pytest tests/secrets/test_mcp_secret_migration.py tests/security/test_mcp_remote_policy.py tests/security/test_mcp_stdio_policy.py -q
uv run pytest tests/integration/test_fresh_install.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
cd web && npm ci && npm run lint && npm run typecheck && npm test -- --run && npm run build
docker build --pull=false -t tianshu:g1-smoke .
uv run pytest tests/integration/test_container_smoke.py -q
git diff --exit-code -- uv.lock
```

说明：`docker build --pull=false` 只在所需 digest 已预拉取时可离线复现；正式 CI 首次拉取仍依赖 registry。

## 10. 外部依赖与本机不能证明的事项

| 事项 | 本机可证明 | 仍需外部证明 |
|---|---|---|
| wheel | 内容、fresh venv、repo 外启动 | 真正从 TestPyPI/PyPI 下载后的 metadata/extra 行为；包名和 Trusted Publishing 配置 |
| Docker | 当前架构本地 build/run（若 daemon 可用） | 干净 Linux runner、amd64/arm64、多种 Docker/Podman；registry digest/availability |
| non-root | 容器 UID、volume 权限 | Kubernetes/OpenShift 任意 UID 策略不在 G1 范围 |
| mock provider | 完全离线、确定性流程 | 不证明任何真实模型质量、限流、计费或 provider 兼容性 |
| real provider | doctor 可做显式 live probe | 外部 API key、额度、区域网络、上游 SLA；不能成为 G1 必需 smoke |
| remote MCP | 负向 policy、no-redirect、fake DNS | 真实 DNS race、TLS、代理、企业 egress firewall；没有 connect-time pin/硬 egress 时不能宣称硬防 rebinding |
| stdio MCP | exact fixture/grant/ExecutionGateway 路径 | 第三方 executable 自身供应链、更新后行为和签名；grant 不是对 server 代码正确性的背书 |
| vulnerability scan | workflow/schema 和已知 finding gate | OSV/npm/CodeQL 数据库实时可用性与零日漏洞 |
| SBOM | 生成、解析、版本一致 | 外部消费者/registry 是否接受，container OS SBOM 完整性 |
| GitHub release | workflow dry run | environment approval、OIDC、branch protection、token permissions、PyPI/GHCR 外部设置 |
| 平台 | Linux/macOS source/wheel matrix | Windows 当前受 `fcntl` 阻断，不能列入支持矩阵 |
| 许可证 | 根 MIT + template SOURCES commit 可核 | vendored 396 templates 的 LICENSE/NOTICE 随 wheel 分发完整性需独立 legal/NOTICE gate |
| secrets backup | 权限、恢复、主动库加密 | 历史 backup 本身仍含旧明文；安全保存/销毁依赖运营策略 |

## 11. 实施前需要拍板的三项边界

1. **G1 是否引入最小持久 `system_audit_events`**：推荐引入；否则必须把 1.6 验收降为结构化日志，不能声称持久系统审计。
2. **secure-remote 的 remote MCP 硬边界**：推荐没有 connect-time pin 或受信 egress proxy/firewall时保持 disabled；不要用两次 DNS preflight 冒充硬防 rebinding。
3. **G1 平台声明**：推荐 Linux + macOS；Windows 等 portable migration lock 完成后再加入，避免 `requires-python` 给出错误的跨平台暗示。

## 12. Phase 1.5–1.6 完成定义

只有以下项目全部有可复现证据，才能勾选 Master Roadmap 1.5–1.6：

- [ ] clean wheel 在 repo 外 cwd、fresh HOME、空 DB 启动，默认 persona、builtin skills、Web 与 executor templates 全部存在。
- [ ] 无真实 key 的显式 demo profile 跑通一条 Native governed Edict，结果明确标记 mock，且无网络调用。
- [ ] doctor 同时提供稳定 JSON 与人类表格，对 config/provider/DB/port/resources/sandbox/deps/MCP 给出准确 exit code。
- [ ] container 以 non-root、锁定依赖和同一 wheel 启动，runtime 无 Node/compiler/无关构建工具。
- [ ] liveness/readiness 分离；DB、scheduler 或 mandatory sandbox 不可用时 readiness 为 503。
- [ ] active MCP DB 不再保存明文 env/header；legacy migration 保数据、失败可恢复、日志/响应无 secret。
- [ ] secure-remote remote MCP 默认拒绝；所有 URL/DNS/redirect 负向用例 fail closed。
- [ ] stdio MCP 默认 disabled，只有 exact command/argv/capability grant 经 ExecutionGateway 才能启动；配置变化撤销 grant。
- [ ] MCP 拒绝有可查询的持久系统审计，或路线图明确降级为结构化日志且不做过度承诺。
- [ ] CI 产生 wheel/container smoke、dependency/code scan 和 Python/Web SBOM；scanner unavailable 阻断发行。
- [ ] SECURITY 与 threat model 明确 trusted-local、secure-remote、MCP、容器、外部执行器、自进化和单节点非保证。
- [ ] release workflow 只产出 Developer Preview，未越权自动公开发布，也未宣称 G2/G4 能力已完成。
