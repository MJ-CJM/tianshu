# 宣发 checklist · Launch Checklist

> 宣发拍板(spec §七):**首发国内**(V2EX/即刻/掘金/公众号),英文 README + 隐喻
> 对照照做但 **HN 留到年末 v0.4 第二波**。质量 gate:迭代 1–3.5 验收全过才宣发。
>
> ⚠️ 带 👤 的项**只能你(维护者)手工做**——发帖、录屏、跑实测、改 GitHub 设置。

## 一、发布前质量 gate(v0.3.0 前必过)

- [x] 迭代 0–3.5 全部合入 main,CI 常绿
- [x] LICENSE(MIT)/ CONTRIBUTING / SECURITY.md 齐
- [x] 英文 README([README.en.md](../../README.en.md))+ 隐喻对照表
- [x] 架构深度博文([blog-architecture.md](blog-architecture.md))
- [x] GIF/视频分镜脚本([demo-storyboards.md](demo-storyboards.md))
- [ ] 👤 成本基线实测跑一周,填 README 数字([cost-baseline.md](cost-baseline.md))
- [ ] 👤 三镜头 GIF 录制 + 压缩,放 README 首屏
- [ ] 👤 宣发视频(客卿驱动 Claude Code)录制;客卿不稳则降级单场面,不推迟

## 二、GitHub 仓库设置(👤 手工)

- [ ] 仓库设为 Public
- [ ] Branch protection:main 勾选 require `backend` + `frontend` CI 通过
- [ ] About:一句话描述 + Topics(`ai-agent` `llm` `governance` `self-improving`
      `fastapi` `claude-code` `mcp` `automation`)
- [ ] 重名排查:GitHub 搜 "tianshu",必要时描述带英文副标锚定搜索
- [ ] Release v0.3.0:附 CHANGELOG 摘要 + GIF

## 三、首发帖(👤 手工发,以下是草稿骨架)

### V2EX / 即刻(标题候选)

- 「你睡觉时,你的 AI 衙门在干活——每一步可批、可审、可回滚」
- 「Claude Code 替你干活,天枢替你管一群 AI 干活(开源)」

### 帖子骨架(三段)

1. **痛点**:天天守着按 approve 很累;放手又怕失控/烧钱/改坏文件。
2. **答案**:天枢=Claude Code 的上级机关。放手四保险(预算熔断/手机批红/影子
   快照回滚/出厂预算护栏)让"敢放手"成立。附镜头 A GIF。
3. **差异化**:不是又一个 agent 框架,是治理×自进化交叉带。MIT 开源,链接。

### 掘金 / 公众号

- 直接发[架构博文](blog-architecture.md)(内容轨道第一篇),讲设计取舍不吹功能。

## 四、发布后(👤)

- [ ] 留 1 周处理反馈(规则 3)
- [ ] 每迭代随 minor 版本发一篇深度技术文(内容轨道,ADR-0006)
- [ ] 北极星盯:周活跃下旨实例数 + 社群人工反馈(opt-in 遥测默认关)
- [ ] HN 第二波弹药攒着:起居注 + 演化 2.0(年末 v0.4)

## 五、成本透明(👤,宣发前)

- [ ] `python scripts/cost_baseline.py --days 7` 得典型月成本区间
- [ ] 填两个 README 的「成本透明」段 + 一句典型任务单次成本示例
