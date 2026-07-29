"""模型目录（models.dev 快照）三级读取：内存 → 磁盘缓存 → 打包快照。

- 打包快照 ``tianshu/resources/models_catalog.json`` 由
  ``scripts/fetch_models_catalog.py`` 生成，保证离线可用。
- ``refresh()`` 手动联网拉取 models.dev 并写磁盘缓存（无自动刷新）。
- 定价统一换算为 CNY/1K（models.dev 原始口径 USD/1M），subscription
  计费的 profile（coding plan）定价归零。
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from tianshu.providers.profiles import BUILTIN_PROFILES, ProviderProfile

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
_SNAPSHOT_RESOURCE = "models_catalog.json"

_default_catalog: ModelCatalog | None = None
_default_catalog_lock = threading.Lock()


def default_catalog() -> ModelCatalog:
    """打包快照驱动的进程级默认目录（无磁盘缓存、缺省汇率）。

    wiring 会用带磁盘缓存与配置汇率的实例替换 cost/tracker 的定价解析器；
    本单例保证未经装配的路径（doctor、单测、脚本）也能拿到目录价。
    """
    global _default_catalog
    if _default_catalog is None:
        with _default_catalog_lock:
            if _default_catalog is None:
                _default_catalog = ModelCatalog()
    return _default_catalog


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    context_window: int | None
    max_output_tokens: int | None
    tool_call: bool
    reasoning: bool
    vision: bool
    # USD / 1M tokens；None = 未知
    cost_input: float | None
    cost_output: float | None
    cost_cache_read: float | None
    release_date: str | None


def _model_from_raw(raw: dict) -> CatalogModel:
    return CatalogModel(
        id=raw.get("id", ""),
        name=raw.get("name", ""),
        context_window=raw.get("context_window"),
        max_output_tokens=raw.get("max_output_tokens"),
        tool_call=bool(raw.get("tool_call")),
        reasoning=bool(raw.get("reasoning")),
        vision=bool(raw.get("vision")),
        cost_input=raw.get("cost_input"),
        cost_output=raw.get("cost_output"),
        cost_cache_read=raw.get("cost_cache_read"),
        release_date=raw.get("release_date"),
    )


class ModelCatalog:
    """线程安全的目录门面；数据源三级降级，全部失败时目录为空但不抛。"""

    def __init__(self, cache_path: Path | None = None, usd_cny_rate: float = 7.2) -> None:
        self._cache_path = cache_path
        self._usd_cny_rate = usd_cny_rate
        self._lock = threading.Lock()
        self._data: dict | None = None
        self._source: str = "none"

    # --- 加载 ---

    def _load_locked(self) -> dict:
        if self._data is not None:
            return self._data
        if self._cache_path is not None and self._cache_path.is_file():
            try:
                self._data = json.loads(self._cache_path.read_text(encoding="utf-8"))
                self._source = "disk_cache"
                return self._data
            except (json.JSONDecodeError, OSError):
                logger.warning("[catalog] 磁盘缓存损坏，回退打包快照: %s", self._cache_path)
        try:
            snapshot = resources.files("tianshu.resources").joinpath(_SNAPSHOT_RESOURCE)
            self._data = json.loads(snapshot.read_text(encoding="utf-8"))
            self._source = "bundled_snapshot"
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            logger.warning("[catalog] 打包快照缺失，目录为空")
            self._data = {"schema_version": 1, "generated_at": None, "providers": {}}
            self._source = "empty"
        return self._data

    def _providers(self) -> dict:
        with self._lock:
            return self._load_locked().get("providers", {})

    # --- 查询 ---

    def models_for(self, profile: ProviderProfile) -> list[CatalogModel]:
        if not profile.models_dev_id:
            return []
        entry = self._providers().get(profile.models_dev_id)
        if not entry:
            return []
        return [_model_from_raw(m) for m in entry.get("models", {}).values()]

    def get_model(self, profile: ProviderProfile, model_id: str) -> CatalogModel | None:
        if not profile.models_dev_id:
            return None
        entry = self._providers().get(profile.models_dev_id)
        if not entry:
            return None
        raw = entry.get("models", {}).get(model_id)
        return _model_from_raw(raw) if raw else None

    def pricing_cny(
        self, profile: ProviderProfile, model_id: str
    ) -> tuple[float, float, float] | None:
        """返回 (input_miss, input_hit, output)，单位 CNY/1K；未知返回 None。

        subscription（coding plan）恒为 (0, 0, 0)；目录缺失时尝试
        litellm.model_cost 兜底。
        """
        if profile.billing == "subscription":
            return (0.0, 0.0, 0.0)
        model = self.get_model(profile, model_id)
        if model is not None and model.cost_input is not None and model.cost_output is not None:
            miss = self._usd_1m_to_cny_1k(model.cost_input)
            out = self._usd_1m_to_cny_1k(model.cost_output)
            hit = (
                self._usd_1m_to_cny_1k(model.cost_cache_read)
                if model.cost_cache_read is not None
                else miss
            )
            return (miss, hit, out)
        return self._litellm_pricing_cny(model_id)

    def _usd_1m_to_cny_1k(self, usd_per_1m: float) -> float:
        return usd_per_1m * self._usd_cny_rate / 1000.0

    def _litellm_pricing_cny(self, model_id: str) -> tuple[float, float, float] | None:
        """litellm 自带 model_cost 表兜底（USD/token）。"""
        try:
            import litellm

            info = litellm.model_cost.get(model_id)
            if not info:
                base = model_id.split("/")[-1]
                info = litellm.model_cost.get(base)
            if not info:
                return None
            in_cost = info.get("input_cost_per_token")
            out_cost = info.get("output_cost_per_token")
            if in_cost is None or out_cost is None:
                return None
            hit_cost = info.get("cache_read_input_token_cost", in_cost)
            rate = self._usd_cny_rate * 1000.0
            return (in_cost * rate, hit_cost * rate, out_cost * rate)
        except Exception:  # noqa: BLE001 - 兜底查询失败等同未知
            return None

    def pricing_cny_by_model(self, model: str) -> tuple[float, float, float] | None:
        """按裸模型串查价（provider 未知时的兜底口径，供 cost/tracker 用）。

        依次尝试完整串与剥掉首个前缀段的串，按 BUILTIN_PROFILES 声明顺序
        扫各 profile 目录（官方直连排在聚合器之前）；目录无命中再落
        litellm.model_cost。
        """
        candidates = [model]
        if "/" in model:
            candidates.append(model.split("/", 1)[1])
        providers = self._providers()
        for profile in BUILTIN_PROFILES:
            if not profile.models_dev_id or profile.billing == "subscription":
                continue
            entry = providers.get(profile.models_dev_id)
            if not entry:
                continue
            models = entry.get("models", {})
            for candidate in candidates:
                if candidate in models:
                    pricing = self.pricing_cny(profile, candidate)
                    if pricing is not None:
                        return pricing
        return self._litellm_pricing_cny(model)

    def status(self) -> dict:
        with self._lock:
            data = self._load_locked()
            providers = data.get("providers", {})
            return {
                "source": self._source,
                "generated_at": data.get("generated_at"),
                "provider_count": len(providers),
                "model_count": sum(len(p.get("models", {})) for p in providers.values()),
            }

    # --- 刷新 ---

    def refresh(self) -> dict:
        """联网拉取 models.dev，裁剪到内置 profile 范围，写磁盘缓存。

        失败抛异常（调用方决定呈现）；成功返回新 status()。
        """
        request = urllib.request.Request(MODELS_DEV_URL, headers={"User-Agent": "tianshu-catalog"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw = json.load(response)
        wanted = {p.models_dev_id for p in BUILTIN_PROFILES if p.models_dev_id}
        providers: dict[str, dict] = {}
        for dev_id in sorted(wanted):
            entry = raw.get(dev_id)
            if not entry:
                continue
            providers[dev_id] = {
                "name": entry.get("name", dev_id),
                "api": entry.get("api", ""),
                "env": entry.get("env", []),
                "doc": entry.get("doc", ""),
                "models": {
                    model_id: self._slim_model(model)
                    for model_id, model in sorted((entry.get("models") or {}).items())
                },
            }
        data = {
            "schema_version": 1,
            "source": MODELS_DEV_URL,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "providers": providers,
        }
        with self._lock:
            if self._cache_path is not None:
                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                self._cache_path.write_text(
                    json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8"
                )
            self._data = data
            self._source = "network"
        return self.status()

    @staticmethod
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
            "cost_input": cost.get("input"),
            "cost_output": cost.get("output"),
            "cost_cache_read": cost.get("cache_read"),
            "cost_cache_write": cost.get("cache_write"),
            "release_date": model.get("release_date"),
        }
