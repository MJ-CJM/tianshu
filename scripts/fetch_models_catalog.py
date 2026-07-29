"""从 models.dev 生成打包快照 resources/models_catalog.json。

只收录 BUILTIN_PROFILES 声明了 models_dev_id 的 provider（约 16 家），
把 4000+ 模型的全量目录裁剪到天枢实际可选的范围，控制快照体积。

用法：
    .venv/bin/python scripts/fetch_models_catalog.py [--source path/to/api.json]

不带 --source 时从 https://models.dev/api.json 抓取。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO_ROOT / "src" / "tianshu" / "resources" / "models_catalog.json"
MODELS_DEV_URL = "https://models.dev/api.json"

sys.path.insert(0, str(REPO_ROOT / "src"))

from tianshu.providers.profiles import BUILTIN_PROFILES  # noqa: E402


def _slim_model(model: dict) -> dict:
    cost = model.get("cost") or {}
    limit = model.get("limit") or {}
    modalities = model.get("modalities") or {}
    return {
        "id": model.get("id", ""),
        "name": model.get("name", ""),
        "context_window": limit.get("context"),
        "max_output_tokens": limit.get("output"),
        "tool_call": bool(model.get("tool_call")),
        "reasoning": bool(model.get("reasoning")),
        "vision": "image" in (modalities.get("input") or []),
        # USD / 1M tokens（models.dev 原始口径；CNY 换算在 model_catalog.py 做）
        "cost_input": cost.get("input"),
        "cost_output": cost.get("output"),
        "cost_cache_read": cost.get("cache_read"),
        "cost_cache_write": cost.get("cache_write"),
        "release_date": model.get("release_date"),
    }


def build_snapshot(source: dict) -> dict:
    wanted = {p.models_dev_id for p in BUILTIN_PROFILES if p.models_dev_id}
    providers: dict[str, dict] = {}
    for dev_id in sorted(wanted):
        entry = source.get(dev_id)
        if not entry:
            print(f"warning: models.dev has no provider {dev_id!r}, skipped", file=sys.stderr)
            continue
        models = {
            model_id: _slim_model(model)
            for model_id, model in sorted((entry.get("models") or {}).items())
        }
        providers[dev_id] = {
            "name": entry.get("name", dev_id),
            "api": entry.get("api", ""),
            "env": entry.get("env", []),
            "doc": entry.get("doc", ""),
            "models": models,
        }
    return {
        "schema_version": 1,
        "source": MODELS_DEV_URL,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "providers": providers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="本地 models.dev api.json 路径（默认联网抓取）")
    args = parser.parse_args()

    if args.source:
        raw = json.loads(Path(args.source).read_text(encoding="utf-8"))
    else:
        request = urllib.request.Request(MODELS_DEV_URL, headers={"User-Agent": "tianshu-catalog"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw = json.load(response)

    snapshot = build_snapshot(raw)
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    total = sum(len(p["models"]) for p in snapshot["providers"].values())
    print(f"wrote {SNAPSHOT_PATH} ({len(snapshot['providers'])} providers, {total} models)")


if __name__ == "__main__":
    main()
