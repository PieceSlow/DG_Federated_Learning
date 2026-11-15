"""M1 演示：数据源画像 Agent（默认 mock，零网络）。

用法::

    python -m governance.agents.demo                     # 用结构契约演示（无患者数据）
    python -m governance.agents.demo --mimic-root "E:\\mimic-iv数据\\mimic-iv-3.1"
    python -m governance.agents.demo --llm off           # 演示"关闭时明确报错"

演示只做两件事：构造可外发视图 → 调用 SourceProfilerAgent → 打印结果与 trace。
不写任何数据产物，不修改流水线行为。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

from governance.agents.base import TraceWriter
from governance.agents.profiler import SourceProfilerAgent
from governance.agents.supervisor import Supervisor
from governance.agents.views import schema_view, stats_view
from governance.config import (
    default_mimic_root,
    default_note_root,
    default_output,
    resolve_data_file,
)
from governance.llm import LLMConfig, LLMUnavailable, build_service

CONTRACT = Path(__file__).resolve().parent / "schema_contract.json"

CORE_TABLES: tuple[tuple[str, str], ...] = (
    ("patients", "hosp"),
    ("admissions", "hosp"),
    ("icustays", "icu"),
    ("diagnoses_icd", "hosp"),
    ("procedures_icd", "hosp"),
    ("prescriptions", "hosp"),
    ("labevents", "hosp"),
)


def _load_preview(mimic_root: Path, rows: int = 50_000) -> dict[str, pd.DataFrame]:
    """只读取表头 + 有限行做统计，数据不落盘、不发往任何服务。"""
    tables: dict[str, pd.DataFrame] = {}
    for name, group in CORE_TABLES:
        path = resolve_data_file(mimic_root, group, name)
        tables[name] = pd.read_csv(path, nrows=rows)
    return tables


def _contract_view() -> dict:
    data = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return {
        "tables": [
            {
                "table": item["table"],
                "columns": [{"name": column} for column in item["columns"]],
            }
            for item in data["tables"]
        ]
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m governance.agents.demo",
        description="M1 演示：数据源画像 Agent（默认 mock，零网络）",
    )
    parser.add_argument("--mimic-root", type=Path, default=None,
                        help="可选；提供后用真实表结构与统计生成视图")
    parser.add_argument("--note-root", type=Path, default=default_note_root(),
                        help="MIMIC-IV-Note 根目录（当前演示不使用文本）")
    parser.add_argument("--output", type=Path, default=default_output(), help="审计输出根目录")
    parser.add_argument("--llm", choices=["off", "mock", "api"], default="mock",
                        help="模型 provider；默认 mock（零网络）")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL", "deepseek-flash"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = LLMConfig(
        provider=args.llm,
        model=args.model,
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
    )
    try:
        service = build_service(config, args.output)
    except LLMUnavailable as exc:
        print(f"无法初始化 LLM：{exc}", file=sys.stderr)
        return 2

    if args.mimic_root is not None:
        tables = _load_preview(args.mimic_root)
        payload = {"schema_view": schema_view(tables), "stats_view": stats_view(tables)}
        source = f"真实数据预览（{len(tables)} 张表，只读）"
    else:
        payload = {
            "schema_view": _contract_view(),
            "stats_view": {"tables": [], "note": "仅结构契约，未提供真实数据"},
        }
        source = "表结构契约（无患者数据）"

    agent = SourceProfilerAgent(
        llm=service,
        trace_writer=TraceWriter(args.output / "audit" / "agent_trace.jsonl"),
    )
    outcome = Supervisor(max_attempts=2).run(agent, payload)

    payload_bytes = len(json.dumps(payload, ensure_ascii=False))
    print(f"provider={config.provider} model={config.model}")
    print(f"视图来源：{source}；{len(payload['schema_view']['tables'])} 张表，"
          f"约 {payload_bytes} 字符（仅结构/统计，不含患者行与文本）")
    print()
    if not outcome.ok or outcome.result is None:
        print("Agent 未产出结果（这是预期的降级行为，不是崩溃）：")
        for item in outcome.history:
            print(json.dumps(item, ensure_ascii=False))
        return 1

    print(f"结果：{outcome.result.summary}")
    print(json.dumps(outcome.result.artifacts, ensure_ascii=False, indent=2))
    if outcome.result.trace is not None:
        print("\ntrace：")
        print(json.dumps(outcome.result.trace.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n审计写入：{args.output / 'audit' / 'agent_trace.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
