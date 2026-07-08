/**
 * 意图预设(迭代 3.5 后 UX 重构,decision Q1)。
 *
 * 把原先埋在「长程任务设置」里的 4 个填充模板,升格为颁敕首屏的「任务类型」卡片。
 * 选一张 = 一键配好一整套合理参数**并收起细节**——预设从「填充」变「模式」。
 *
 * fields 会 setFieldsValue 到表单(字段名与 EdictForm/AcceptanceConfigSection 一致);
 * longTask / assignMode 是 React state,由 applyPreset 单独驱动。
 * 分析上下文见 docs/strategy/2026-07-08-edict-form-ux-analysis.md。
 */

export interface EdictPreset {
  key: string;
  icon: string;
  /** i18n key 后缀:preset.<key>.label / preset.<key>.summary */
  longTask: boolean;
  assignMode?: "auto" | "direct";
  fields: Record<string, unknown>;
}

export const EDICT_PRESETS: EdictPreset[] = [
  {
    key: "quick",
    icon: "⚡",
    longTask: false,
    assignMode: "auto",
    fields: { review_policy: "always", executor: "native", priority: "normal" },
  },
  {
    key: "analysis",
    icon: "📊",
    longTask: true,
    assignMode: "auto",
    fields: {
      review_policy: "on_flag",
      executor: "native",
      execution_profile: "foreground",
      max_outer_iterations: 5,
      on_exhaustion: "escalate",
      on_critic_unavailable: "skip",
      same_issue_threshold: 2,
    },
  },
  {
    key: "coding",
    icon: "💻",
    longTask: true,
    assignMode: "auto",
    fields: {
      review_policy: "on_flag",
      executor: "native",
      execution_profile: "checkpointed",
      max_outer_iterations: 8,
      deadline_hours: 1,
      deadline_minutes: 0,
      on_exhaustion: "escalate",
      on_critic_unavailable: "escalate",
      same_issue_threshold: 2,
    },
  },
  {
    key: "research",
    icon: "🔬",
    longTask: true,
    assignMode: "auto",
    fields: {
      review_policy: "on_flag",
      executor: "native",
      execution_profile: "background",
      max_outer_iterations: 15,
      min_outer_iterations: 4,
      critic_strictness: "strict",
      deadline_hours: 2,
      deadline_minutes: 0,
      on_exhaustion: "best_effort",
      on_critic_unavailable: "skip",
      same_issue_threshold: 3,
    },
  },
  {
    key: "keqing",
    icon: "🤝",
    longTask: false,
    assignMode: "auto",
    fields: { review_policy: "always", executor: "keqing:claude-code", priority: "normal" },
  },
];

export function getPreset(key: string | null): EdictPreset | undefined {
  return key ? EDICT_PRESETS.find((p) => p.key === key) : undefined;
}
