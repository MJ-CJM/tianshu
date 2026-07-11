# 开源发布阶段门 · Launch Checklist

> **当前版本：0.4.2。** 当前能力以[能力事实矩阵](capability-matrix.md)为准；设计稿、历史 ADR 或“已交付”记录不自动构成稳定能力承诺。
>
> 本清单按 G0–G5 的证据顺序推进，不用日历或尚未创建的版本号代替验收。**G1 只允许 Developer Preview；G5 才允许正式开源宣发。**带 👤 的动作只能由维护者执行。

## G0 事实与语言门

- [x] 用户可见治理术语统一为“裁决 / Decision”，内部兼容 key、事件名和 API 不改名
- [x] v0.4.2 版本标记一致，能力矩阵列出成熟度、默认值、保证、非保证、证据和目标 Gate
- [x] 两语种 README 明确可信本地边界，不宣称 opaque CLI 全治理、真实在线 challenger、可信自动晋升或安全沙箱
- [x] 桌面原型保留既定品牌壳，并通过真实性、裁决理由和演化强门测试
- [x] SQLite 基线迁移具备 ledger、升级前在线备份和离线安全恢复证据
- [x] 后端、生产 Web、桌面原型与文档真实性检查通过
- [ ] 👤 审批 G0 验收包；未审批不得进入 G1

## G1 Developer Preview

只有 G1 安全门通过后，才可以标记 Developer Preview：

- [ ] REST、WebSocket、MCP 和 Web 共用明确的身份与授权边界
- [ ] public/local 运行模式默认安全，危险工作区变更经过统一执行边界
- [ ] Executor Capability Manifest 对 Native 与外部 adapter 的保证逐项可验证
- [ ] fresh install、doctor、容器启动和安全发行检查在外部环境复验
- [ ] 发布说明只承诺 public-safe foundation；不使用“敢放手”或“自进化闭环已完成”

## G2 Durable Governance & Evidence

- [ ] pending Decision、任务状态与调度在定义的故障点可恢复
- [ ] managed 边界内的副作用有幂等键、receipt 与可验证账本
- [ ] Evidence Bundle 可导出并解释执行、裁决、成本、产物与限制
- [ ] 通知投递失败有持久重试和可观察状态

## G3 Desktop Web Productization

- [ ] 中枢总览、敕令详情、演化中心接入 G2 真实 API
- [ ] 四组十四部门的信息架构、主题和侧栏折叠能力保持一致
- [ ] 桌面 E2E、可访问性、视觉回归和性能门禁通过
- [ ] 👤 对真实页面而非静态原型做产品验收

## G4 Governed Evolution

- [ ] 真实 challenger 路由与样本门禁有端到端证据
- [ ] Native 与至少一个 external managed adapter 遵守同一治理契约
- [ ] 晋升、回滚、技能供应链和记忆收益评估可复验
- [ ] 只有 G4 通过后，才表述“自进化闭环已成立”

## G5 正式宣发

G3 与 G4 均通过后，才进入正式开源宣发：

- [ ] 三个外部环境完成从安装到黄金 Demo 的快速开始
- [ ] release 制品、SBOM、签名、版本与文档可复现
- [ ] LICENSE、CONTRIBUTING、SECURITY、维护与响应边界完整
- [ ] 👤 仓库设为 Public，并配置 branch protection、About、Topics 与 social preview
- [ ] 👤 使用真实环境录制桌面产品演示；画面和解说逐项对应能力矩阵
- [ ] 👤 发布中英文材料；不使用未经对比证据支持的市场唯一性结论
- [ ] 👤 发布后预留反馈与安全响应窗口

## 宣发素材前置检查

- [ ] 成本基线来自可重复脚本和明确样本，不用占位数字
- [ ] Demo 中的 Native、Keqing、飞书与 Telegram 边界分别标注
- [ ] 所有截图、GIF 和视频只展示已经通过对应 Gate 的产品能力
- [ ] 每条核心承诺都能回链到能力矩阵中的 Evidence
