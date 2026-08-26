# 客卿管理页：当前实验边界

> 当前状态：**实验能力，已由“天工院 → 客卿馆”可发现**。本页描述已落地行为；完整 pi/凭证网关方案见
> [pi-default-adapter.md](pi-default-adapter.md)，该文件是历史/演进设计，不是完成声明。
> 本体论背景见
> [执行主体本体论](../domain-model.md#6-执行主体本体论百官内臣-vs-客卿外臣)。

## 当前可用

- `/keqing` 路由可直接访问，也可由“天工院 → 客卿馆”进入。
- `GET /api/keqing/status` 只读检查本机 CLI 是否可发现、可读取的安装版本、pi pinned
  version 漂移和声明能力，并投影已经存在的 Pi EXECUTOR 治理候选；检查不 spawn CLI、
  不运行漂移巡检，也不创建候选。
- 页面可配置各 backend 默认模型和 per-run 预算提示。
- Pi 行可查看 current/last-good generation、活跃 run 数和 durable candidate，并跳转演化中心。
- 当前 executor 只使用 CLI 自管凭证。页面不读取、不输入、不存储 raw provider key。

## 当前不可用

- Keqing 凭证网关没有接入生产 executor。状态 API 固定报告
  `gateway_enabled=false`，`PUT /api/agent-config` 尝试开启时返回 `409`。
- 因此目前没有通过天枢网关实现的 hard cost cap、模型 allowlist 强制、raw-key 隔离或
  provider 请求级计费保证。
- worktree、guard、scoped token、before-provider headers 和完整 session RPC 闭环的设计
  不能当作所有 backend 的已验证能力。
- Keqing 不属于开源黄金路径，也不参与“普通任务/长程任务已可用”的默认结论。

## 产品约束

客卿是外部执行器，不增加 SOUL/ROLE 或京察能力。Pi 行只提供已存在治理候选的只读入口；
候选的 Gate、canary、Decision、晋升和回滚统一由演化中心与 PromotionService 承担。管理页
不拥有提案、stage 或 activate 副作用，并始终把“自管凭证”和“实验状态”展示给用户。

**相关实现**：`gateway/keqing_api.py`、`gateway/config_api.py`、
`web/src/pages/KeqingManagementPage.tsx`、`web/src/router/AppRoutes.tsx`。
