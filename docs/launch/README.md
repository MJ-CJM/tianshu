# 开源交付工具包 · Launch Kit

当前版本为 **0.4.2**。本目录先服务于 G0–G5 的证据准备，不代表已经进入正式宣发；只有 G5 通过后才执行发布动作。所有功能表述以[能力事实矩阵](capability-matrix.md)为准。

| 材料 | 用途 | 当前状态 |
|---|---|---|
| [capability-matrix.md](capability-matrix.md) | v0.4.2 的成熟度、保证、非保证与证据 | G0 事实源 |
| [../../README.en.md](../../README.en.md) | 英文公开入口 | 已按事实边界校正 |
| [metaphor-map.md](metaphor-map.md) | 明制隐喻 ↔ 工程实体对照 | 已按“裁决”术语校正 |
| [blog-architecture.md](blog-architecture.md) | 架构取舍说明 | G0 技术稿，不是发布稿 |
| [demo-storyboards.md](demo-storyboards.md) | 当前可诚实演示的桌面与渠道分镜 | G0 内部验收稿 |
| [cost-baseline.md](cost-baseline.md) + [scripts/cost_baseline.py](../../scripts/cost_baseline.py) | 成本区间测算方法与脚本 | 待维护者实测 |
| [checklist.md](checklist.md) | G0–G5 发布阶段门 | 按 Gate 推进 |

## 一句话定位

> 天枢是一个可治理、可验证、持续成长的自进化 Agent OS。

这条定位描述产品方向。v0.4.2 当前只承诺可信本地边界：Native 工具治理有限稳定；外部 Claude Code/Codex CLI 为 `contained + experimental`；持久裁决、公共远程鉴权、真实 challenger 和可信自动晋升分别留待后续 Gate。

## 当前叙事顺序

1. **治理有边界**：先说明 Native 与 external CLI 的不同保证。
2. **验证有证据**：时间线、成本台账、评估报告都能回链到实现和测试，同时写明非保证。
3. **成长有门禁**：记忆、画像、技能候选与 Universe 是实验能力；效果和晋升必须被证明。
4. **中国隐喻服务工程**：四组十四部门帮助理解职责，不替代清晰的 API、权限和成熟度模型。

## 需要维护者执行的动作（👤）

外部环境复验、成本实测、仓库设置、真实录制和发布都由维护者在对应 Gate 后执行，详见[发布阶段门](checklist.md)。任何素材出现与矩阵冲突时，先修产品或降级文案，不通过剪辑补齐不存在的能力。
