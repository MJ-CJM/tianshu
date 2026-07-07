"""Fuzzy find-and-replace engine for skill patching.

8-strategy matching chain, tried in order. First hit wins.
Ported from hermes-agent's fuzzy_match.py pattern.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class FuzzyMatchResult:
    start: int
    end: int
    strategy: str


# --------------- strategies ---------------


def _exact(content: str, pattern: str) -> FuzzyMatchResult | None:
    idx = content.find(pattern)
    if idx == -1:
        return None
    return FuzzyMatchResult(idx, idx + len(pattern), "exact")


def _line_trimmed(content: str, pattern: str) -> FuzzyMatchResult | None:
    """Strip trailing whitespace from each line, then match."""
    norm_c = "\n".join(line.rstrip() for line in content.split("\n"))
    norm_p = "\n".join(line.rstrip() for line in pattern.split("\n"))
    idx = norm_c.find(norm_p)
    if idx == -1:
        return None
    # Map back to original positions
    return _map_back(content, norm_c, idx, len(norm_p), "line_trimmed")


def _whitespace_normalized(content: str, pattern: str) -> FuzzyMatchResult | None:
    """Collapse runs of whitespace to single space, then match."""
    norm_c = re.sub(r"[ \t]+", " ", content)
    norm_p = re.sub(r"[ \t]+", " ", pattern)
    idx = norm_c.find(norm_p)
    if idx == -1:
        return None
    return _map_back(content, norm_c, idx, len(norm_p), "whitespace_normalized")


def _indentation_flexible(content: str, pattern: str) -> FuzzyMatchResult | None:
    """Ignore leading whitespace on each line."""
    c_lines = content.split("\n")
    p_lines = pattern.split("\n")
    stripped_c = [line.lstrip() for line in c_lines]
    stripped_p = [line.lstrip() for line in p_lines]

    if len(p_lines) == 0:
        return None

    # Slide window
    for i in range(len(stripped_c) - len(stripped_p) + 1):
        if stripped_c[i : i + len(stripped_p)] == stripped_p:
            start = sum(len(c_lines[j]) + 1 for j in range(i))
            end = sum(len(c_lines[j]) + 1 for j in range(i + len(stripped_p))) - 1
            return FuzzyMatchResult(start, end, "indentation_flexible")
    return None


def _escape_normalized(content: str, pattern: str) -> FuzzyMatchResult | None:
    """Normalize escape sequences: literal \\n → actual newline."""
    norm_p = pattern.replace("\\n", "\n").replace("\\t", "\t")
    if norm_p == pattern:
        return None  # No escapes to normalize
    idx = content.find(norm_p)
    if idx == -1:
        return None
    return FuzzyMatchResult(idx, idx + len(norm_p), "escape_normalized")


def _trimmed_boundary(content: str, pattern: str) -> FuzzyMatchResult | None:
    """Strip leading/trailing blank lines from pattern, then match."""
    trimmed = pattern.strip("\n")
    if trimmed == pattern:
        return None  # Nothing to trim
    idx = content.find(trimmed)
    if idx == -1:
        return None
    return FuzzyMatchResult(idx, idx + len(trimmed), "trimmed_boundary")


def _unicode_normalized(content: str, pattern: str) -> FuzzyMatchResult | None:
    """NFC normalization + smart quotes → ASCII quotes."""
    norm_c = _normalize_unicode(content)
    norm_p = _normalize_unicode(pattern)
    if norm_c == content and norm_p == pattern:
        return None  # No change
    idx = norm_c.find(norm_p)
    if idx == -1:
        return None
    return _map_back(content, norm_c, idx, len(norm_p), "unicode_normalized")


def _block_anchor(content: str, pattern: str) -> FuzzyMatchResult | None:
    """Match by first + last non-empty lines as anchors, verify middle ≥ 50% similarity."""
    p_lines = [ln.strip() for ln in pattern.split("\n") if ln.strip()]
    if len(p_lines) < 2:
        return None

    first_line = p_lines[0]
    last_line = p_lines[-1]
    c_lines = content.split("\n")

    for i, cl in enumerate(c_lines):
        if cl.strip() != first_line:
            continue
        # Search for last line after first
        for j in range(i + len(p_lines) - 1, min(i + len(p_lines) * 2, len(c_lines))):
            if j >= len(c_lines):
                break
            if c_lines[j].strip() != last_line:
                continue
            # Check middle similarity
            candidate = [ln.strip() for ln in c_lines[i : j + 1]]
            if _line_similarity(p_lines, candidate) >= 0.5:
                start = sum(len(c_lines[k]) + 1 for k in range(i))
                end = sum(len(c_lines[k]) + 1 for k in range(j + 1)) - 1
                return FuzzyMatchResult(start, end, "block_anchor")
    return None


# --------------- helpers ---------------


_SMART_QUOTES = {
    "\u2018": "'",  # '
    "\u2019": "'",  # '
    "\u201c": '"',  # "
    "\u201d": '"',  # "
    "\u2013": "-",  # –
    "\u2014": "--",  # —
    "\u2026": "...",  # …
}


def _normalize_unicode(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for smart, ascii_ in _SMART_QUOTES.items():
        text = text.replace(smart, ascii_)
    return text


def _line_similarity(a_lines: list[str], b_lines: list[str]) -> float:
    """Fraction of lines in a that appear in b."""
    if not a_lines:
        return 1.0
    b_set = set(b_lines)
    matches = sum(1 for line in a_lines if line in b_set)
    return matches / len(a_lines)


def _map_back(
    original: str,
    normalized: str,
    norm_start: int,
    norm_len: int,
    strategy: str,
) -> FuzzyMatchResult | None:
    """Best-effort mapping from normalized positions back to original string.

    When normalization is character-preserving (same length), positions map 1:1.
    Otherwise, use line-based heuristic.
    """
    if len(original) == len(normalized):
        return FuzzyMatchResult(norm_start, norm_start + norm_len, strategy)

    # Line-based mapping: find which lines the match spans in normalized
    norm_before = normalized[:norm_start]
    norm_match = normalized[norm_start : norm_start + norm_len]
    start_line = norm_before.count("\n")
    match_lines = norm_match.count("\n") + 1

    orig_lines = original.split("\n")
    if start_line >= len(orig_lines):
        return None

    end_line = min(start_line + match_lines, len(orig_lines))
    orig_start = sum(len(orig_lines[i]) + 1 for i in range(start_line))
    orig_end = sum(len(orig_lines[i]) + 1 for i in range(end_line)) - 1
    return FuzzyMatchResult(orig_start, max(orig_start, orig_end), strategy)


# --------------- public API ---------------

_STRATEGIES = [
    _exact,
    _line_trimmed,
    _whitespace_normalized,
    _indentation_flexible,
    _escape_normalized,
    _trimmed_boundary,
    _unicode_normalized,
    _block_anchor,
]


def fuzzy_find(content: str, pattern: str) -> FuzzyMatchResult | None:
    """Try strategies in order. Return first match or None."""
    for strategy in _STRATEGIES:
        result = strategy(content, pattern)
        if result is not None:
            return result
    return None


def fuzzy_replace(content: str, old: str, new: str) -> tuple[str, str]:
    """Replace using fuzzy matching.

    Returns (new_content, strategy_used).
    Raises ValueError if no match found.
    """
    match = fuzzy_find(content, old)
    if match is None:
        raise ValueError(f"Pattern not found (tried {len(_STRATEGIES)} strategies)")
    replaced = content[: match.start] + new + content[match.end :]
    return replaced, match.strategy
