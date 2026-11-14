"""环境与数据路径自检：python scripts/check_env.py

新环境跑治理流水线前先执行这个脚本：会检查 Python 版本、依赖、MIMIC 数据目录、
MEDS schema 副本是否就位，并给出可操作的修复建议。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance import config  # noqa: E402

REQUIRED = {
    "pandas": "数据读取与加工",
    "pyarrow": "parquet 读写",
    "langgraph": "阶段编排由 LangGraph StateGraph 承载",
    "flexible_schema": "按 MEDS 官方 schema 校验产物",
}
OPTIONAL = {
    "PIL": "对象图谱绘图（tools 下的可选脚本）",
    "jsonschema": "flexible-schema 的间接依赖，通常随依赖装好",
}


def mark(ok: bool) -> str:
    return "[ OK ]" if ok else "[FAIL]"


def main() -> int:
    problems: list[str] = []

    print("== Python ==")
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = sys.version_info >= (3, 10)
    print(f"{mark(ok)} Python {version}（要求 >= 3.10）")
    if not ok:
        problems.append("Python 版本过低，请升级到 3.10 及以上")

    print("\n== 必需依赖 ==")
    for module, reason in REQUIRED.items():
        ok = importlib.util.find_spec(module) is not None
        print(f"{mark(ok)} {module:18s} {reason}")
        if not ok:
            problems.append(f"缺少依赖 {module}：python -m pip install -r requirements.txt")

    print("\n== 可选依赖 ==")
    for module, reason in OPTIONAL.items():
        ok = importlib.util.find_spec(module) is not None
        print(f"{mark(ok)} {module:18s} {reason}")

    print("\n== 数据与规范 ==")
    checks = [
        ("MIMIC_ROOT", config.default_mimic_root(), config.require_mimic_root),
        ("MIMIC_NOTE_ROOT", config.default_note_root(), config.require_note_root),
        ("MEDS schema", config.meds_src(), config.require_meds_src),
    ]
    for label, path, checker in checks:
        try:
            checker(path)
            print(f"{mark(True)} {label:16s} {path}")
        except FileNotFoundError as exc:
            print(f"{mark(False)} {label:16s} {path}")
            print(f"         {str(exc).splitlines()[0]}")
            problems.append(f"{label} 未就绪：{str(exc).splitlines()[0]}")

    print(f"       输出目录         {config.default_output()}")

    print()
    if problems:
        print("需要先处理以下问题：")
        for item in problems:
            print(f" - {item}")
        return 1
    print("环境就绪：python run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
