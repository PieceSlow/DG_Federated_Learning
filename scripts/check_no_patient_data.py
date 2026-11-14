"""提交前启发式检查：仓库里是否混入了患者级数据。

规则：
1. 出现数据类文件（parquet/csv/csv.gz/zip）直接判失败；
2. 文本文件里出现 8 位数字（MIMIC 的 subject_id / hadm_id 量级，1xxxxxxx / 2xxxxxxx）
   给出警告并判失败，提示人工确认。

用法：python scripts/check_no_patient_data.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

BLOCKED_SUFFIXES = {".parquet", ".csv", ".gz", ".xlsx", ".zip", ".pptx", ".docx"}
SKIP_DIRS = {".git", "output", "__pycache__", ".venv", "venv", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".ps1", ".js", ".yaml", ".yml", ".toml", ".cfg", ".ini"}
ID_PATTERN = re.compile(r"\b([12]\d{7})\b")
# 允许出现 8 位数字的上下文：示例占位、版本号说明
ALLOW_LINE_MARKERS = ("示例", "例如", "TODO", "placeholder", "版本")


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        files.append(path)
    return sorted(files)


def main() -> int:
    blocked: list[Path] = []
    suspicious: list[tuple[Path, int, str]] = []

    for path in iter_files():
        relative = path.relative_to(REPO_ROOT)
        if path.suffix.lower() in BLOCKED_SUFFIXES or path.name.endswith(".csv.gz"):
            blocked.append(relative)
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if len(text) > 2_000_000:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            match = ID_PATTERN.search(line)
            if not match:
                continue
            if any(marker in line for marker in ALLOW_LINE_MARKERS):
                continue
            suspicious.append((relative, number, line.strip()[:120]))

    if blocked:
        print("发现数据类文件（禁止提交）：")
        for path in blocked:
            print(f"  - {path}")

    if suspicious:
        print("发现疑似患者标识（8 位数字），请人工确认：")
        for path, number, line in suspicious[:50]:
            print(f"  - {path}:{number}: {line}")
        if len(suspicious) > 50:
            print(f"  ... 其余 {len(suspicious) - 50} 处省略")

    if blocked or suspicious:
        print("\n处理方式见 docs/04-数据与合规.md；不要用白名单绕过。")
        return 1

    print(f"检查通过：{REPO_ROOT.name}/ 内未发现数据类文件或疑似患者标识。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
