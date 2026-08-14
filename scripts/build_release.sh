#!/usr/bin/env bash
# 构建发行 wheel：前端 → 后端，一条命令可复现。
#
# 为什么需要它：src/tianshu/web/static/ 是 vite 产物且被 gitignore，干净检出里
# 并不存在。直接 `uv build` 会得到一个没有界面的 wheel——所以构建后端对缺失的
# Web 载荷 fail-closed，而正确的构建顺序由这个脚本固定下来（而不是靠口头约定）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$ROOT/dist}"

cd "$ROOT/web"
npm ci
npm run build

cd "$ROOT"
# 后端会自行清空 setuptools 暂存树；这里显式再清一次，让脚本单独使用时行为一致。
rm -rf "$ROOT/build"
uv build --wheel --out-dir "$OUT_DIR"

echo
echo "wheel → $OUT_DIR"
ls -1 "$OUT_DIR"/tianshu_agent_os-*.whl
