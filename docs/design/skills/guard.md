# 技能安全 Guard — 威胁扫描与信任矩阵

> 设计意图：技能内容（尤其 community / agent 自建）可能藏恶意指令或隐蔽载荷，安装/写入前必须按信任等级扫描拦截。借鉴 hermes-agent 的 skills_guard。

## 1. 扫描产物

`SkillsGuard.scan_content(content, trust_level)` → `GuardResult(verdict, findings)`：
- `verdict`：`safe` / `caution` / `dangerous`
- `findings`：`GuardFinding(category, severity, message, line_number, snippet)` 列表

verdict 判定：有 `CRITICAL` → dangerous；有 finding 但无 critical → caution；无 finding → safe。

## 2. 威胁类别（13 类）

`GuardCategory` 覆盖 13 类，约 50+ 条 `THREAT_PATTERNS` regex：

| 类别 | 典型模式 | 代表 severity |
|---|---|---|
| EXFILTRATION 外泄 | `curl ... $KEY/$TOKEN`、读 `.env/.netrc`、`printenv`、DNS 外泄 | CRITICAL/HIGH |
| PROMPT_INJECTION 注入 | `ignore previous instructions`、role hijack、`DAN mode`、hidden div、`do not tell the user` | CRITICAL/HIGH |
| DESTRUCTIVE 破坏 | `rm -rf /`、`dd of=/dev/`、`shutil.rmtree('/...')` | CRITICAL/HIGH |
| PERSISTENCE 驻留 | `authorized_keys`、`/etc/sudoers`、launchd、改 `CLAUDE.md/AGENTS.md` | CRITICAL/MEDIUM |
| network (REVERSE_SHELL) | `nc -l`、`/bin/sh -i >/dev/tcp/`、ngrok、webhook.site | CRITICAL/HIGH |
| OBFUSCATION 混淆 | `base64 -d \|`、`eval('...')`、`echo ... \| bash`、chr() 拼接 | CRITICAL/HIGH |
| SUPPLY_CHAIN 供应链 | `curl ... \| sh`、`curl ... \| python`、unpinned pip/npm | CRITICAL/MEDIUM |
| credential_exposure 凭证 | `sk-...`、`ghp_...`、`AKIA[0-9A-Z]{16}`、`api_key=...` | CRITICAL |
| traversal 穿越 | `../../../`、`/etc/passwd` `/etc/shadow` | HIGH/CRITICAL |
| mining 挖矿 | `xmrig`/`cgminer`/`minerd` 等 | CRITICAL |
| privilege_escalation 提权 | `NOPASSWD`、`chmod u+s`、`setcap` | CRITICAL/HIGH |

## 3. 无形 Unicode 检测

`scan_invisible_unicode` 逐字符扫 `INVISIBLE_CHARS`（17 个）：zero-width space/joiner、word joiner、invisible times/separator/plus、BOM、LTR/RTL embedding & override（U+202A–U+202E）、isolate（U+2066–U+2069）。命中即记 HIGH finding（位置 + `U+XXXX`），防止用隐藏字符夹带可见正文之外的指令。

## 4. 信任等级与安装策略矩阵

`TrustLevel`：`builtin` / `trusted` / `community` / `agent-created`。`resolve_trust_level(source)` 映射：
- `agent-created` → AGENT_CREATED
- `official*` / `builtin` → BUILTIN
- `workspace` / `user` → TRUSTED
- 其余 → COMMUNITY

`should_allow(result, trust_level)` 查 `INSTALL_POLICY`，按 (safe, caution, dangerous) 取动作，仅 `allow` 放行：

| TrustLevel | safe | caution | dangerous |
|---|---|---|---|
| BUILTIN | allow | allow | allow |
| TRUSTED | allow | allow | **block** |
| COMMUNITY | allow | **block** | **block** |
| AGENT_CREATED | allow | allow | **ask** |

设计意图：community 来源最严（caution 即拦），builtin 全放行，agent 自建对 dangerous 走人工确认（ask）而非直接拦——因为 Agent 可能因合理原因写入命中模式的内容。

## 5. 触发点

- Skill candidate 包快照、安装和晋升前（按来源 resolve trust level）；
- Loader 内部资源写原语被治理服务调用时；
- 兼容测试可显式注册 `skill_manage` 来验证 Guard，但生产 Agent 当前不公开该工具；
- Reviewer/Curator 自动写链尚未接通，默认关闭并在 LLM 调用前跳过。

**相关实现**：[../../impl/skills/](../../impl/skills/)
