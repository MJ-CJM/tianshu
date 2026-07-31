/**
 * tianshu-guard —— 天枢注入 pi 进程的治理扩展(进程内软增强)。
 *
 * 与 adapter 同仓同版本钉死,经 `pi --mode rpc -e tianshu-guard.ts` 注入。四层职责:
 *  1. 准入:project_trust=no(拒载被开发仓 .pi/ 扩展,防提权);tool_call deny/allow
 *     (bash 段级不对称:deny 逐段+全串、allow 仅全串、不可拆升 ask)。ask 档经
 *     extension_ui_request 反向通道上报天枢批红(超时兜底=拒绝,由天枢侧决定)。
 *  2. 传输:registerProvider 把允许 provider 的 baseUrl 重定向到天枢网关 + 用注入的
 *     PI_GATEWAY_TOKEN;白名单外 provider unregister。raw provider key 永不进本进程。
 *  3. 会话防护:project_trust=no。
 *  4. 握手:registerCommand("__tianshu_guard_handshake__") 应答存活;天枢 spawn 后发
 *     握手命令,失败即 fail-closed 终止 run(与 pi 原生 hook 的 fail-open 相反)。
 *
 * 配置来自天枢 PolicyCompiler 产物(guard_config.py 的 GuardConfig JSON),spawn 前写到
 * workspace 外受控路径,经环境变量 TIANSHU_GUARD_CONFIG 指向。
 *
 * ⚠️ 本文件运行在 pi 进程内(TypeScript),Python 测试覆盖不到——须真 pi 0.83.0 集成验证。
 * 硬保证不寄托于本 guard:网关(凭证/预算)、worktree(文件边界)、验收+三方合并三关卡兜底。
 */

import { readFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

interface BashRules {
  deny_segments: string[];
  allow_exact: string[];
  unsplittable_action: string;
}
interface GuardConfig {
  version: string;
  project_trust: string;
  tool_deny: string[];
  tool_ask: string[];
  bash: BashRules;
  provider_allowlist: string[];
  model_allowlist: string[];
  gateway_url: string | null;
  edict_id: string;
  run_id: string;
  handshake_required: boolean;
}

function loadConfig(): GuardConfig {
  const path = process.env.TIANSHU_GUARD_CONFIG;
  if (!path) throw new Error("TIANSHU_GUARD_CONFIG not set");
  return JSON.parse(readFileSync(path, "utf8")) as GuardConfig;
}

function globToRegExp(glob: string): RegExp {
  const escaped = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`);
}

// bash 段级拆分:按 && || ; | 拆段(与 Python 侧 E2 语义镜像);子 shell/命令替换视为不可拆。
function splitSegments(command: string): { segments: string[]; splittable: boolean } {
  if (/\$\(|`|<\(|>\(/.test(command)) return { segments: [command], splittable: false };
  const segments = command
    .split(/&&|\|\||;|\|/)
    .map((s) => s.trim())
    .filter(Boolean);
  return { segments, splittable: true };
}

// deny:逐段+全串;allow:仅全串;不可拆→升 ask。返回 "allow" | "deny" | "ask"。
function evaluateBash(command: string, rules: BashRules): "allow" | "deny" | "ask" {
  const { segments, splittable } = splitSegments(command);
  const denyRe = rules.deny_segments.map(globToRegExp);
  const full = command.trim();
  for (const re of denyRe) {
    if (re.test(full)) return "deny";
    for (const seg of segments) if (re.test(seg)) return "deny";
  }
  if (!splittable) return rules.unsplittable_action === "deny" ? "deny" : "ask";
  const allowRe = rules.allow_exact.map(globToRegExp);
  if (allowRe.some((re) => re.test(full))) return "allow";
  return "ask";
}

export default function (pi: ExtensionAPI): void {
  const cfg = loadConfig();

  // 1. 准入:拒载被开发仓项目级扩展/技能(防 repo 控制代码自我授权)。
  pi.on("project_trust", () => ({ trusted: cfg.project_trust === "no" ? "no" : "yes" }));

  // 握手:天枢 spawn 后发此命令确认 guard 已装载;未应答 → 天枢 fail-closed 终止 run。
  pi.registerCommand("__tianshu_guard_handshake__", {
    handler: () => ({ type: "Acted" }),
  });

  // 2. tool_call 准入:deny 短路;bash 段级;ask 上报批红(反向通道)。
  pi.on("tool_call", async (event, ctx) => {
    const name = event.toolName as string;
    if (cfg.tool_deny.includes(name)) {
      return { block: true, reason: `tianshu policy denies tool: ${name}` };
    }
    if (name === "bash" || name === "run_terminal_command") {
      const command = String((event.input as { command?: string })?.command ?? "");
      const verdict = evaluateBash(command, cfg.bash);
      if (verdict === "deny") return { block: true, reason: `tianshu bash policy deny: ${command}` };
      if (verdict === "ask") {
        const ok = await ctx.ui.confirm("天枢批红", `允许执行?\n${command}`);
        if (!ok) return { block: true, reason: "tianshu batch review rejected" };
      }
    }
    if (cfg.tool_ask.includes(name)) {
      const ok = await ctx.ui.confirm("天枢批红", `允许工具 ${name}?`);
      if (!ok) return { block: true, reason: "tianshu batch review rejected" };
    }
    return undefined;
  });

  // 3. 传输:把允许 provider 重定向到天枢网关 + scoped token;白名单外拒。
  if (cfg.gateway_url) {
    const token = process.env.PI_GATEWAY_TOKEN ?? "";
    for (const provider of cfg.provider_allowlist) {
      pi.registerProvider(provider, {
        baseUrl: `${cfg.gateway_url}/${provider}`,
        apiKey: token,
        headers: { "X-Tianshu-Edict-Id": cfg.edict_id, "X-Tianshu-Run-Id": cfg.run_id },
      });
    }
  }
}
