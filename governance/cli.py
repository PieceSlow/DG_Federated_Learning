"""命令行入口：``python run.py`` 与 ``python -m governance`` 共用。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from governance import pipeline
from governance.config import (
    Settings,
    default_mimic_root,
    default_note_root,
    default_output,
    require_mimic_root,
    require_note_root,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="医疗多源数据治理流水线：接入 → 标准化 → 校验 → 汇聚 → 对齐 → 对象化 → 标签 → 分级 → 编目",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--mimic-root", type=Path, default=default_mimic_root(),
                        help="MIMIC-IV 结构化数据根目录")
    parser.add_argument("--note-root", type=Path, default=default_note_root(),
                        help="MIMIC-IV-Note 根目录")
    parser.add_argument("--output", type=Path, default=default_output(), help="治理产物根目录")
    parser.add_argument("--cohort-size", type=int, default=100, help="队列患者数")
    parser.add_argument("--max-notes-per-subject", type=int, default=3,
                        help="每名患者最多使用几份出院小结")
    parser.add_argument("--stages", default=None,
                        help="只运行指定阶段（逗号分隔，如 assemble,align）；默认全流程")
    parser.add_argument("--list-stages", action="store_true", help="列出阶段及其依赖后退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_stages:
        print(pipeline.describe_stages())
        return 0

    try:
        require_mimic_root(args.mimic_root)
        require_note_root(args.note_root)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    stages = None
    if args.stages:
        stages = [item.strip() for item in args.stages.split(",") if item.strip()]

    settings = Settings(
        mimic_root=args.mimic_root,
        note_root=args.note_root,
        output=args.output,
        cohort_size=args.cohort_size,
        max_notes_per_subject=args.max_notes_per_subject,
    )
    try:
        pipeline.run(settings, only=stages)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"治理失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - 交互场景
        print("已中断，审计日志与已完成阶段的产物保留。", file=sys.stderr)
        return 130
    return 0
