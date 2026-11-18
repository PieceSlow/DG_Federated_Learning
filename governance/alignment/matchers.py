"""四个匹配器：字符串、类型、结构、值域（本地统计）。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from governance.alignment.schema import FieldView, family_of

_SUFFIXES = ("id", "time", "date", "name", "type", "code", "value", "status")

#: 同义 token：让 label/title/display/drug 这类"可读名"能互相匹配
_SYNONYMS = {
    "title": "label",
    "name": "label",
    "description": "label",
    "display": "label",
    "drug": "label",
    "num": "value",
    "numeric": "value",
}


def name_tokens(name: str) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name))
    text = text.replace("valuenum", "value num")
    parts = [part for part in re.split(r"[_\W]+", text.lower()) if part]
    tokens: set[str] = set()
    for part in parts:
        matched = False
        for suffix in _SUFFIXES:
            if part.endswith(suffix) and len(part) > len(suffix):
                tokens.add(part[: -len(suffix)])
                tokens.add(suffix)
                matched = True
                break
        if not matched:
            tokens.add(part)
    return {_SYNONYMS.get(token, token) for token in tokens}


def string_score(source: FieldView, target: FieldView) -> float:
    if source.name.lower() == target.name.lower():
        return 1.0
    source_tokens, target_tokens = name_tokens(source.name), name_tokens(target.name)
    if not source_tokens or not target_tokens:
        return 0.0
    jaccard = len(source_tokens & target_tokens) / len(source_tokens | target_tokens)
    sequence = SequenceMatcher(None, source.name.lower(), target.name.lower()).ratio()
    return round(0.6 * jaccard + 0.4 * sequence, 4)


def type_score(source: FieldView, target: FieldView) -> float:
    source_family, target_family = family_of(source.dtype), family_of(target.dtype)
    if source_family == target_family:
        return 1.0
    if {source_family, target_family} == {"int", "float"}:
        return 0.8
    if "text" in (source_family, target_family):
        return 0.3
    return 0.1


def structure_score(source: FieldView, target: FieldView) -> float:
    source_tokens, target_tokens = name_tokens(source.name), name_tokens(target.name)
    if not source_tokens or not target_tokens:
        return 0.0
    if source_tokens & target_tokens:
        # 共同后缀（_id / _time / _code）比泛化词更像同一类字段
        if len(source_tokens & target_tokens) >= 2:
            return 1.0
        return 0.5
    return 0.0


def value_score(source: FieldView, target: FieldView) -> float:
    """值域只使用本地聚合统计（类型族 + 空值率），不发送任何取值。"""
    base = type_score(source, target)
    if target.name.lower().endswith("_id") and source.null_rate > 0:
        base *= 0.5
    return round(base, 4)
