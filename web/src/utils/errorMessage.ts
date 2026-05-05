/**
 * 把 orchestrator 的错误码字符串解析为中文友好提示。
 *
 * 当前覆盖：
 * - "budget_exhausted: <field> (usage_ratio=<ratio>)" → "⏱️/🔢/💴 X 预算已用尽（已用 N%）"
 * - "outer loop exhausted" → "已达最大迭代次数"
 * - "aborted by human" → "人工中止"
 * - "exhausted + aborted" → "已达最大迭代次数后被人工中止"
 * - "budget_exhausted: time (...)" 等老格式回退到泛型解析
 */

const FIELD_LABEL: Record<string, { icon: string; cn: string }> = {
  tokens: { icon: "🔢", cn: "Token" },
  cost: { icon: "💴", cn: "费用" },
  time: { icon: "⏱️", cn: "时间" },
};

export interface ParsedError {
  /** 中文标题，用于醒目展示 */
  headline: string;
  /** 原始错误字符串，备查 */
  raw: string;
  /** 是否为预算耗尽类错误（用于决定图标/色彩） */
  isBudget: boolean;
}

const BUDGET_RE = /^budget_exhausted(?::\s*(\w+))?\s*(?:\(usage_ratio=([\d.]+)\))?/;

export function parseErrorMessage(raw: string | null | undefined): ParsedError | null {
  if (!raw) return null;
  const trimmed = raw.trim();

  // budget_exhausted: <field> (usage_ratio=<ratio>)
  const budget = trimmed.match(BUDGET_RE);
  if (budget) {
    const field = budget[1];
    const ratio = budget[2] ? Number(budget[2]) : null;
    const meta = field ? FIELD_LABEL[field] : null;
    let headline: string;
    if (meta) {
      const pct = ratio != null ? ` (已用 ${Math.round(ratio * 100)}%)` : "";
      headline = `${meta.icon} ${meta.cn}预算已用尽${pct}`;
    } else {
      headline = "💸 预算已用尽";
    }
    return { headline, raw, isBudget: true };
  }

  if (trimmed === "outer loop exhausted") {
    return { headline: "🔁 已达最大迭代次数", raw, isBudget: false };
  }
  if (trimmed === "aborted by human") {
    return { headline: "🛑 人工中止", raw, isBudget: false };
  }
  if (trimmed === "exhausted + aborted") {
    return { headline: "🛑 达最大迭代次数后被人工中止", raw, isBudget: false };
  }
  if (trimmed.startsWith("checks 配置错")) {
    return { headline: "⚙️ acceptance.checks 配置错误", raw, isBudget: false };
  }
  if (trimmed.startsWith("orchestrator error")) {
    return { headline: "⚠️ 长任务运行时错误", raw, isBudget: false };
  }

  // 兜底
  return { headline: trimmed, raw, isBudget: false };
}
