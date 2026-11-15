"""出院小结的确定性章节与事件抽取（文本分支）。

大模型在本机批量抽取不现实（14B 模型约 4-5 token/s，且输出不稳定，见 docs/03），
因此文本环节使用可复现、可审计的规则：

* 按 MIMIC 出院小结的标准章节头切分；
* 每份小结产出一条 ``NOTE//DISCHARGE`` 事件携带原文，每个非空章节产出一条
  ``NOTE_SECTION//<KEY>``；
* 出院诊断段落逐行产出 ``NOTE_DIAGNOSIS//DISCHARGE``；
* 出院用药段落对编号行解析"药名 + 剂量"，产出 ``NOTE_MEDICATION//<NAME>``；
* 每条诊断/用药行附带断言标签：present / negated / family / historical / hypothetical。

全部规则化，无网络调用、无模型调用。
"""

from __future__ import annotations

import re
from typing import Any

# Canonical MIMIC-IV-Note discharge-summary headers (upper-cased, colon stripped).
KNOWN_HEADERS: set[str] = {
    "CHIEF COMPLAINT",
    "HISTORY OF PRESENT ILLNESS",
    "PAST MEDICAL HISTORY",
    "PAST SURGICAL HISTORY",
    "SOCIAL HISTORY",
    "FAMILY HISTORY",
    "PHYSICAL EXAM",
    "PERTINENT RESULTS",
    "BRIEF HOSPITAL COURSE",
    "MEDICATIONS ON ADMISSION",
    "DISCHARGE MEDICATIONS",
    "DISCHARGE DISPOSITION",
    "DISCHARGE DIAGNOSIS",
    "DISCHARGE CONDITION",
    "DISCHARGE INSTRUCTIONS",
    "ALLERGIES",
    "MAJOR SURGICAL OR INVASIVE PROCEDURE",
}

HEADER_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ,/()'\-]{2,60}):\s*(.*)$")
NUMBERED_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(.+)$")
DOSE_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9\-/ ]{1,70}?)\s+(\d+(?:\.\d+)?)\s*"
    r"(mg|mcg|g|ml|meq|units?|%|tablet|capsule)s?\b",
    re.IGNORECASE,
)

MAX_NOTE_CHARS = 20_000
MAX_SECTION_CHARS = 4_000
MAX_LINE_CHARS = 500

#: 断言线索（顺序即优先级：否定 > 家族史 > 历史性 > 假设）
ASSERTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("negated", re.compile(r"\b(no|not|without|denies|denied|never|negative for|no evidence of|absent)\b", re.IGNORECASE)),
    ("family", re.compile(r"\b(family history|mother|father|sibling|parent|brother|sister)\b", re.IGNORECASE)),
    ("historical", re.compile(r"\b(history of|prior|previous|past medical history)\b", re.IGNORECASE)),
    ("hypothetical", re.compile(r"\b(if|should|would|possible|rule out|suspect)\b", re.IGNORECASE)),
)


def detect_assertion(text: str) -> str:
    """给诊断/用药行打断言标签；单标签，优先级见 ``ASSERTION_PATTERNS``。"""
    for name, pattern in ASSERTION_PATTERNS:
        if pattern.search(text or ""):
            return name
    return "present"


def _section_key(header: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", header.upper()).strip("_")


def _clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_sections(note_text: str) -> dict[str, str]:
    """Return {HEADER: section text} using only known discharge headers."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in note_text.splitlines():
        line = raw_line.rstrip()
        match = HEADER_RE.match(line)
        if match:
            header = _clean_line(match.group(1)).upper()
            if header in KNOWN_HEADERS:
                current = header
                sections.setdefault(current, [])
                inline = _clean_line(match.group(2))
                if inline:
                    sections[current].append(inline)
                continue
        if current is not None:
            sections[current].append(line)
    return {header: "\n".join(lines).strip() for header, lines in sections.items()}


def _drug_name(line: str) -> str:
    match = DOSE_RE.match(line)
    if match:
        name = _clean_line(match.group(1))
    else:
        words = _clean_line(line).split()
        name = " ".join(words[:2]) if words else ""
    name = re.sub(r"^\d+[.)]\s*", "", name)
    return name[:80].strip(" -;,")


def _drug_slug(name: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return slug[:40] or "UNKNOWN"


def extract_note_events(note_text: str, max_medications: int = 40) -> list[dict[str, Any]]:
    """Return a list of {code, text_value} extracted from one discharge note."""
    extracted: list[dict[str, Any]] = []
    sections = split_sections(note_text or "")

    for header, body in sections.items():
        if not body:
            continue
        key = _section_key(header)
        extracted.append(
            {"code": f"NOTE_SECTION//{key}", "text_value": body[:MAX_SECTION_CHARS],
             "assertion": "present"}
        )

        if header == "DISCHARGE DIAGNOSIS":
            for line in body.splitlines():
                cleaned = _clean_line(line)
                if cleaned and not re.fullmatch(r"[\d.\-–]+", cleaned):
                    extracted.append(
                        {"code": "NOTE_DIAGNOSIS//DISCHARGE",
                         "text_value": cleaned[:MAX_LINE_CHARS],
                         "assertion": detect_assertion(cleaned)}
                    )

        if header == "DISCHARGE MEDICATIONS":
            count = 0
            for line in body.splitlines():
                numbered = NUMBERED_RE.match(line)
                if not numbered:
                    continue
                drug = _drug_name(numbered.group(2))
                if not drug:
                    continue
                extracted.append(
                    {
                        "code": f"NOTE_MEDICATION//{_drug_slug(drug)}",
                        "text_value": _clean_line(numbered.group(2))[:MAX_LINE_CHARS],
                        "assertion": detect_assertion(line),
                        "name": drug,
                    }
                )
                count += 1
                if count >= max_medications:
                    break

    return extracted
