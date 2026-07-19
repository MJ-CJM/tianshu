export const NAV_GROUPS = [
  {
    label: "敕令",
    items: [
      { label: "御书房", icon: "audit", target: "control", count: 1 },
      { label: "文书房", icon: "schedule" },
    ],
  },
  {
    label: "政要",
    items: [
      { label: "内阁", icon: "crown" },
      { label: "廷议", icon: "team" },
      { label: "都察院", icon: "certificate" },
      { label: "权印司", icon: "safety" },
    ],
  },
  {
    label: "百官",
    items: [
      { label: "百官阁", icon: "officials" },
      { label: "文渊阁", icon: "book" },
      { label: "位面", icon: "universe", target: "evolution" },
      { label: "考成", icon: "experiment" },
    ],
  },
  {
    label: "外朝",
    items: [
      { label: "藏兵阁", icon: "tool" },
      { label: "鸿胪寺", icon: "global" },
      { label: "通政司", icon: "message" },
      { label: "户部账房", icon: "cost" },
    ],
  },
];

export const controlMetrics = [
  { label: "办理中", value: "03", tone: "cyan", hint: "2 条按计划推进" },
  { label: "待裁决", value: "01", tone: "red", hint: "1 项高风险裁决" },
  { label: "今日预算", value: "¥ 18.60", tone: "gold", hint: "上限 ¥ 96.00" },
  { label: "证据完整率", value: "97 / 100", tone: "green", hint: "过去 24h · 缺 3 项" },
];

export const activeEdicts = [
  {
    id: "ED-1042",
    title: "开源发布安全加固",
    executor: "客卿 · Codex",
    department: "工部执行",
    progress: 68,
    status: "等待裁决",
    statusTone: "review",
    milestone: "MCP stdio 启动外部进程，需要确认权限范围",
  },
  {
    id: "ED-1039",
    title: "整理开源发布文档与演示案例",
    executor: "礼部执笔",
    department: "验收标准 4 项",
    progress: 42,
    status: "办理中",
    statusTone: "running",
    milestone: "正在生成安装验证证据",
  },
];

export const edict = {
  id: "ED-1042",
  title: "开源发布安全加固",
  issuer: "由御书房于 13:08 颁发",
  executor: "客卿 · Codex",
  workspace: "workspace: tianshu",
  risk: "需要复核",
  riskDetail: "stdio · shell · network",
  budget: "¥18.60 / ¥30.00",
  tokens: "token 42k / 80k",
  deadline: "今日 18:00",
  remaining: "剩余 03:27:42",
  tabs: ["总览", "计划", "脉络", "证据", "变更", "裁决", "成本", "结案"],
  timeline: [
    {
      state: "done",
      title: "计划已确认",
      time: "13:08",
      detail: "拆分为鉴权、命令隔离、持久裁决、回归测试四个工作包",
    },
    {
      state: "done",
      title: "策略检查通过",
      time: "13:16",
      detail: "只允许修改 src/tianshu/gateway 与 tests/security",
    },
    {
      state: "current",
      title: "等待高风险工具裁决",
      time: "14:28",
      detail: "申请启动本地测试进程，网络权限保持关闭",
    },
    {
      state: "pending",
      title: "独立验收",
      time: "—",
      detail: "完成后由都察院验证 12 个安全用例",
    },
  ],
  decision: {
    title: "允许执行本地测试命令？",
    command: "uv run pytest tests/security",
    constraints: ["网络：关闭", "环境变量：净化", "超时：180s"],
  },
  evidence: [
    { label: "变更证据", value: "6 files · +184 / -37", hint: "全部位于授权目录" },
    { label: "验证证据", value: "8 / 12 passed", hint: "待执行 4 个命令隔离用例" },
    { label: "治理证据", value: "3 允许 · 1 待裁决", hint: "本地验收数据 · 未接入真实操作者身份" },
  ],
};

export const evolution = {
  promotionGate: {
    current: 18,
    required: 50,
  },
  candidate: {
    version: "v0.5.0-alpha.2",
    title: "治理策略 + 技能召回优化",
    source: "来自 48 次开源发布任务复盘",
    hypothesis: "先验证证据完整度，再提高自治执行比例",
    rollback: "checkpoint-evo-041",
    score: "92.4",
    baseline: "88.7",
  },
  pipeline: [
    { label: "提案", state: "done", meta: "变更差异已封存" },
    { label: "离线回归", state: "done", meta: "48 / 48" },
    { label: "安全评测", state: "done", meta: "12 / 12" },
    { label: "Canary", state: "current", meta: "18 / 50" },
    { label: "人工晋升", state: "pending", meta: "待裁决" },
  ],
  comparisons: [
    { label: "任务达成", champion: "88.7", candidate: "92.4", delta: "+3.7" },
    { label: "高风险阻断", champion: "96.1%", candidate: "99.2%", delta: "+3.1%" },
    { label: "证据完整度", champion: "84.0%", candidate: "96.8%", delta: "+12.8%" },
    { label: "平均成本", champion: "¥7.42", candidate: "¥6.94", delta: "-6.4%" },
  ],
  suites: [
    { label: "历史回归集", value: "48 / 48", tone: "pass", hint: "无已知能力退化" },
    { label: "高风险用例", value: "12 / 12", tone: "pass", hint: "命令、网络、凭证全部通过" },
    { label: "Canary 样本", value: "18 / 50", tone: "active", hint: "达到 50 个样本后可人工晋升" },
    { label: "回滚演练", value: "2 / 2", tone: "pass", hint: "平均恢复 11.4 秒" },
  ],
};
