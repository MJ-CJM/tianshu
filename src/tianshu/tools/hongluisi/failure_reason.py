"""引擎异常 → 可读归因。

`reason` 会一路传到事件流与鸿胪寺「最近访问」表，是用户排障时唯一能看到的线索。
只写异常类名等于把话说了一半：真实踩到过——装好 scrapling 后抓取仍次次失败，
事件流只显示 `engine_exception:ImportError`，看上去像两个免费引擎都坏了，实际是
`trafilatura` 缺席、两者都倒在 HTML→Markdown 那一步。`markdown_extract` 本来写了
「请执行: pip install 'tianshu[web]'」，却在这里被压成了类名（issue #68）。
"""

from __future__ import annotations

#: reason 要能进日志与表格单元格，过长的 message 截断
MAX_REASON_CHARS = 240


def describe_engine_exception(exc: BaseException) -> str:
    """把引擎抛出的异常翻成用户看得懂、照着做得动的一句话。

    缺依赖单独归一类：它不是"引擎坏了"，而是"少装了东西"，且修复动作明确。
    """
    message = str(exc).strip()
    if isinstance(exc, ImportError):  # 含 ModuleNotFoundError
        prefix = "missing_dependency"
        # 上游没给 message 时也要保住分类，别退回泛泛的 engine_exception
        detail = message or type(exc).__name__
    else:
        prefix = f"engine_exception:{type(exc).__name__}"
        if not message:
            return prefix
        detail = message
    reason = f"{prefix}:{detail}"
    if len(reason) <= MAX_REASON_CHARS:
        return reason
    return reason[: MAX_REASON_CHARS - 1] + "…"
