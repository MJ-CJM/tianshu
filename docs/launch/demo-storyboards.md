# Lean Developer Preview · 单一黄金 Demo 分镜

> **历史流程档案：** 本文记录开源前内部发布流程的当时状态，其中的审批旗标与授权表述已随仓库公开而完成流转，不再具有效力。当前能力口径以 [docs/CURRENT-STATE.md](../CURRENT-STATE.md) 为准。

> 本分镜是本地 desktop Web only 的候选验收说明，不是公开宣传脚本。唯一 runner 命令
> 只放在 [使用指南](../usage/lean-developer-preview.md)，避免出现多个互相漂移的入口。
>
> `design_status`: `approved`；`implementation_status`: `verified_local`；
> `visual_status`: `user_approval_pending`；`publication_status`: `not_authorized`。

## 冻结产品壳层

- 生产品牌资产：[`web/public/brand.png`](../../web/public/brand.png)，SHA-256
  `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`。
- 格言：“成功只有一个——按照自己的方式，去度过人生。”
- 右上五项：“彩蛋 / 通用 / English / 实时 / 通政”。
- 左侧默认六个一级入口：“中枢 / 御书房 / 朝堂 / 百司 / 天工院〔实验〕 / 内府”，深浅主题与
  收起控制保留。御书房包含全部敕令、颁发敕令、钦天监、都察院；朝堂包含吏部、廷议、
  内阁；百司包含翰林院、鸿胪寺、通政司；内府保留藏兵阁、权印司、户部账房。
- 天工院显示“实验”，其中演化司、诸界台、客卿馆显示“实验”，考功司显示“试行”。
- 中枢四项主指标固定为“当前执行中 / 未归档敕令 / 待裁决总数 / 累计证据束（含归档）”；
  未归档敕令显示待后续指令与已撤回分项，当前执行中为 `0` 不代表任务工作台为空。
- 中枢“独特能力”区域展示长程治理、自进化、平行位面、客卿四张卡；自进化卡读取
  权威快照的真实 `evolution_status`，不使用写死的演示状态。
- 历史 S4 深度证据覆盖中枢总览、敕令详情和演化中心；当前 final source 已在隔离
  Demo/Eval 环境完成六入口、首次引导、真实 404 和主要操作的网页功能点验。现有 48 张
  基线及哈希仍保留自前一版 6 路由产品壳；最新 7 路由、预期 56 张视觉图片尚未重新
  生成或更新哈希。

## 当前产品壳验收镜头

| 镜头 | 画面 | 必须说明的事实 |
|---|---|---|
| A. 中枢 | 四项治理指标和“独特能力”四张卡 | 当前执行中与未归档敕令口径分离；证据累计含归档；普通主体为本人范围、管理员为全局范围；页面以前台 5 秒兜底轮询和相关 WS 事件失效重拉保持更新；长程治理为稳定（有限边界），自进化、平行位面、客卿为实验，自进化当前状态来自 `evolution_status` |
| B. 御书房 | 默认查看全部任务，再按状态缩窄 | 当前主体可见且未归档的任务不会因没有待裁决而消失；叠加标签区分定时、长程、对话和客卿等类型；进度来自最新执行事实 |
| C. 朝堂与百司 | 依次展开朝堂、百司 | 朝堂展示吏部、廷议、内阁；百司展示翰林院、鸿胪寺、通政司；同一时间只展开一个一级分组 |
| D. 天工院 | 依次进入演化司、诸界台、考功司、客卿馆 | 演化司、诸界台、客卿馆为“实验”，考功司为“试行”；可发现不等于生产承诺 |
| E. 内府 | 展开内府 | 藏兵阁、权印司、户部账房名称保持不变 |
| F. 视觉证据 | 深浅主题、侧栏展开/收起、两个桌面视口 | 48 张保留证据覆盖前一版 6 路由；最新 7 路由预期 56 张，尚未生成或更新哈希，画面仍待用户审批 |

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

- 历史黄金路径为 retained `implemented` evidence；当前 final source 未重跑 Ubuntu
  fresh-HOME exact-Wheel runner，不能建立新 Candidate。S5 整体仍是
  `experimental` Lean Core。
- S4 阶段自动化视觉证据已保留，但新的 Candidate final Gate 尚待执行；用户终审是
  `user_approval_pending`，VoiceOver 是 `external_pending`。
- remote MCP/open stdio MCP 为 `disabled`；official container/PyPI/GHCR 为
  `deferred`。
- OpenHands、ROI、cost calibration 和 full G4 为 `external_pending`；full G5 为
  `deferred`。
- 不录制外部渠道、移动端或实验子页的伪深度；只展示“天工院”中真实可达、
  带成熟度与边界说明的状态；不展示 mock 数字，不暗示外部发布已授权。
