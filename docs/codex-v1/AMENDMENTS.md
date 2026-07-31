# Codex v1 快照修订记录

`codex-v1` 最初由 commit
`7386cf3dd93d2d6f30c0438966814cac49d5ff31` 于 2026-07-12 写入。该 commit 中
`MANIFEST.sha256` 文件本身的 SHA-256 是
`79f9e9cee4b2e3db65fbf6da9d085cc80e0d22741ec5c4f1fadad586558bb77b`；
需要复核原始字节时，以该 commit 为准。

后续仓库历史已经移动过 `STATUS.md`、`DEVELOPMENT-HANDOFF.md`，并修订过
`plans/12-g2-durable-governance-evidence.md`，但没有同步旧 manifest。2026-07-31 的
文档收口做了以下有限修订：

- 为入口、状态、验证、能力与 UI 索引补充“历史快照”提示；
- 修复文件移动后失效的相对链接，以及一个已移除前端组件的证据链接；
- 将 `MANIFEST.sha256` 更新为当前目录中这些修订后文件的校验清单。

历史 `evidence/` 报告正文、时间戳、批准截图和图片资产没有为了匹配当前实现而改写。
当前能力与验证结论以 [`../CURRENT-STATE.md`](../CURRENT-STATE.md) 和
[`../launch/capability-matrix.md`](../launch/capability-matrix.md) 为准。
