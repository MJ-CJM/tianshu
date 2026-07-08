"""Diagnostician(太医)— 从失败奏折与审计意见中提炼代码演化假设。

只诊断、不动刀:输出演化域内的 (target_path, hypothesis) 清单,
交由 UniverseEvolver.propose_code_variant 走既有「分支→变异→门禁→评估」闭环。
失败安全:无症状 / LLM 输出非法 / 全部越界 → 返回空列表。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from tianshu.universe.code_mutator import _within_evolvable

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是天枢的「太医」,负责诊断平台自身代码的病灶。"
    "给定近期失败任务的症状(目标/错误/审计意见)与已尝试过的假设,"
    "提出最多 {k} 条新的代码改进假设,每条瞄准演化域内的一个文件,"
    "严禁与已尝试假设方向重复。只输出 JSON 数组,不带 markdown 代码块标记。"
)

_USER = """\
近期失败症状:
{failures}

已尝试过的假设(避免重复方向):
{tried}

演化域(target_path 必须落在其中;目录以 / 结尾表示前缀):
{evolvable}

输出 JSON 数组,每项:
{{"target_path": "src/tianshu/...", "hypothesis": "改什么、为何能减少上述失败", "rationale": "对应哪些症状"}}
无可提之处输出 []。"""

_MEMORIAL_TYPE = "taiyi.memorial"
_MEMORIAL_TITLE = "太医奏折"
_HEALTHY_SUMMARY = "太医巡诊,未见沉疴。"


def compile_memorial(diagnoses: list[dict]) -> dict:
    """把 diagnose 的诊断清单汇编成面向用户的「太医奏折」。

    太医的第二出口:纯函数,不碰 storage/LLM。即便自进化关闭,用户
    也能凭此奏折一览太医对平台健康的判断;无诊断项则呈报未见沉疴。

    :param diagnoses: diagnose 的输出,每项含 target_path 与 hypothesis。
    :return: 奏折 dict——type / title / summary / findings / count。
    """
    findings = [
        {
            "target": str(d.get("target_path") or ""),
            "hypothesis": str(d.get("hypothesis") or ""),
        }
        for d in diagnoses
    ]
    count = len(findings)
    summary = _HEALTHY_SUMMARY if count == 0 else f"太医巡诊,察得 {count} 处可调之症,详列于后。"
    return {
        "type": _MEMORIAL_TYPE,
        "title": _MEMORIAL_TITLE,
        "summary": summary,
        "findings": findings,
        "count": count,
    }


class Diagnostician:
    def __init__(self, llm_client: Any, storage: Any, *, evolvable_paths: tuple[str, ...]) -> None:
        self._llm = llm_client
        self._storage = storage
        self._evolvable = tuple(evolvable_paths)

    async def diagnose(self, *, max_hypotheses: int = 3) -> list[dict]:
        failures = self._collect_failures()
        if not failures:
            return []
        prompt = _USER.format(
            failures=failures,
            tried=self._tried_hypotheses(),
            evolvable="\n".join(f"- {p}" for p in self._evolvable),
        )
        raw = await self._ask_llm(prompt, max_hypotheses)
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target_path") or "")
            hypothesis = str(item.get("hypothesis") or "").strip()
            if not target or not hypothesis:
                continue
            if not _within_evolvable(target, self._evolvable):
                logger.info("diagnose: drop out-of-evolvable proposal %s", target)
                continue
            out.append(
                {
                    "target_path": target,
                    "hypothesis": hypothesis,
                    "rationale": str(item.get("rationale") or ""),
                }
            )
            if len(out) >= max_hypotheses:
                break
        return out

    async def report(self, *, max_hypotheses: int = 5) -> dict:
        """太医的第二出口:汇编面向用户的健康奏折(诊断摘要)。

        调 diagnose 取诊断清单,再交 compile_memorial 汇编成奏折。失败安全:
        diagnose 抛异常或无症状,皆回落为 count=0 的空奏折,绝不外抛。

        :param max_hypotheses: 最多纳入奏折的诊断条数。
        :return: 奏折 dict,结构同 compile_memorial。
        """
        try:
            diagnoses = await self.diagnose(max_hypotheses=max_hypotheses)
        except Exception:  # noqa: BLE001
            logger.warning("report: diagnose failed, empty memorial", exc_info=True)
            diagnoses = []
        return compile_memorial(diagnoses)

    def _collect_failures(self, limit: int = 30) -> str:
        """近期失败 memorial 的症状行:goal / error / 审计意见。"""
        try:
            result = self._storage.list_memorials(status="failed", limit=limit)
        except Exception:  # noqa: BLE001
            logger.warning("diagnose: list_memorials failed", exc_info=True)
            return ""
        # 真实 storage.list_memorials 返回 (rows, total);测试替身直接返回 rows 本身,两种都兼容。
        mems = result[0] if isinstance(result, tuple) else result
        lines: list[str] = []
        for m in mems:
            edict = self._storage.get_edict(m.edict_id)
            goal = (getattr(edict, "goal", "") or "")[:120]
            err = (getattr(m, "error", "") or "")[:200]
            audit = "; ".join(self._audit_reasons(m))[:200]
            lines.append(f"- goal: {goal}\n  error: {err}\n  audit: {audit}")
        return "\n".join(lines)

    @staticmethod
    def _audit_reasons(m: Any) -> list[str]:
        """审计意见 reasons 列表。

        真实 Memorial.audit 是 AuditResult(reasons: list[str]) | None；
        兼容旧/测试替身的 audit_json 属性(dict,或未反序列化的 JSON 字符串)。
        """
        audit_obj = getattr(m, "audit", None)
        if audit_obj is not None:
            return [str(r) for r in (getattr(audit_obj, "reasons", None) or [])]
        aj = getattr(m, "audit_json", None)
        if isinstance(aj, str):
            try:
                aj = json.loads(aj)
            except (json.JSONDecodeError, TypeError, ValueError):
                aj = None
        if isinstance(aj, dict):
            return [str(r) for r in aj.get("reasons", [])]
        return []

    def _tried_hypotheses(self, limit: int = 20) -> str:
        unis = self._storage.list_universes(include_archived=True)
        rows = [u for u in unis if u.get("origin") == "code_variant" and u.get("description")]
        rows.sort(key=lambda u: u.get("created_at") or "", reverse=True)
        return "\n".join(f"- {u['description'][:150]}" for u in rows[:limit]) or "(无)"

    async def _ask_llm(self, prompt: str, k: int) -> Any:
        messages = [
            {"role": "system", "content": _SYSTEM.format(k=k)},
            {"role": "user", "content": prompt},
        ]
        for _ in range(3):
            try:
                resp = await self._llm.chat(messages=messages)
                text = (getattr(resp, "content", None) or "").strip()
                if text.startswith("```") and "\n" in text:
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                prompt += "\n\n上次输出非合法 JSON,严格只输出 JSON 数组。"
                messages[-1] = {"role": "user", "content": prompt}
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1)
        return []
