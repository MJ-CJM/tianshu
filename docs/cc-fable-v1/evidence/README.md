# 证据档案说明

本目录保存 cc-fable-v1 批次（`20260719T083725Z-01da3844dde7`）的发布证据：

- `builds/*/provenance.json` — 构建来源与产物哈希
- `gates/*/manifest.json` — 门禁命令、退出码与日志哈希
- `lean-preview/*/` — Lean Preview 13 步验证场景的逐步产物与不可变报告

**原始执行日志未随公开仓库分发**（体积大且包含构建机本地路径），已移至维护者私有归档。
`manifest.json` / `provenance.json` 中记录的 `log_sha256` 哈希仍然有效——如需复核任一
日志的完整性，可向维护者索取归档件并对照哈希验证。证据链的权威主体是本目录内的
JSON 清单及其内容哈希，而非原始日志文本。
