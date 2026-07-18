# Lean Developer Preview · 单一黄金 Demo 分镜

> 本分镜是本地 desktop Web only 的候选验收说明，不是公开宣传脚本。唯一 runner 命令
> 只放在 [使用指南](../usage/lean-developer-preview.md)，避免出现多个互相漂移的入口。

## 冻结产品壳层

- 生产品牌资产：[`web/public/brand.png`](../../web/public/brand.png)，SHA-256
  `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`。
- 格言：“成功只有一个——按照自己的方式，去度过人生。”
- 右上五项：“彩蛋 / 通用 / English / 实时 / 通政”。
- 左侧保留四组十四部门导航、深浅主题与收起控制。
- 深度承诺仅覆盖中枢总览、敕令详情、演化中心三张核心真实页。

## 一条 13 步证据故事

| 镜头 | 画面 | 必须说明的事实 |
|---|---|---|
| 1. 就绪 | 本地桌面 Web 与已认证主体 | exact Wheel、fresh HOME、loopback；源码与 Wheel hash 已绑定 |
| 2. 敕令 | 提交受治理敕令 | 用户术语为“敕令”；不是聊天记录或 mock 卡片 |
| 3. 裁决 | 观察待裁决并附理由解决 | 持久 Decision 权威；空理由会被拒绝 |
| 4. 运行与 Evidence | 奏折完成并下载 closed Evidence Bundle v1 | 展示 bundle/content hash，不用单一 success badge 替代证据 |
| 5. 技能候选与门禁 | 提议技能候选并完成 evidence-bound Gate | 只展示技能候选；代码候选不晋升 |
| 6. 分流 | canary-eligible run 获得真实 candidate overlay | assignment 在 dispatch 前持久化；不是永远返回 champion 的假分流 |
| 7. 回滚 | 新流量归零，新 run 使用 champion | 保留回滚 receipt 与既有 assignment 证据 |
| 8. 严格校验 | verifier 重算 report/artifact hash 与 provenance | 13 步全部 passed 才接受批次；失败批次不可覆盖 |

## 状态与禁区

- 黄金路径为 `implemented`；S5 整体仍是 `experimental` Lean Core。
- 自动化视觉通过，但用户终审是 `user_approval_pending`；VoiceOver 是
  `external_pending`。
- remote MCP/open stdio MCP 为 `disabled`；official container/PyPI/GHCR 为
  `deferred`。
- OpenHands、ROI、cost calibration、full G4/full G5 为 `external_pending` 或
  `deferred`。
- 不录制外部渠道、移动端或十四部门伪深度；不展示 mock 数字；不暗示外部发布已授权。
