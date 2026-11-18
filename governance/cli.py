"""命令行入口：``python run.py`` 与 ``python -m governance`` 共用。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from governance import pipeline
from governance.llm import Budget, LLMConfig, LLMUnavailable, build_service, load_env_file
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
    parser.add_argument("--llm", choices=["off", "mock", "api", "ollama"],
                        default=os.environ.get("LLM_PROVIDER", "off"),
                        help="智能体推理内核：off（默认，确定性建图）/ mock / api / "
                             "ollama（本机 Ollama 原生接口，关闭思考）")
    parser.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", "deepseek-flash"))
    parser.add_argument("--llm-base-url", default=os.environ.get("LLM_BASE_URL"),
                        help="OpenAI 兼容地址；本机 Ollama 用 http://127.0.0.1:11434/v1")
    parser.add_argument("--llm-timeout", type=float, default=None,
                        help="单次调用超时（秒）；默认 api=60、ollama=900")
    parser.add_argument("--llm-max-tokens", type=int,
                        default=int(os.environ.get("LLM_MAX_TOKENS", "4000")),
                        help="单次输出上限；思考型模型（如 qwen3）建议 8000，否则推理会吃光额度")
    parser.add_argument("--llm-budget-cny", type=float,
                        default=float(os.environ.get("LLM_BUDGET_CNY", "20")))
    parser.add_argument("--llm-budget-usd", type=float, default=None,
                        help="可选：按美元设预算；与 --llm-budget-cny 同时给时取更小值")
    parser.add_argument("--llm-usd-cny", type=float,
                        default=float(os.environ.get("LLM_USD_CNY", "7.2")))
    parser.add_argument("--llm-max-calls", type=int,
                        default=int(os.environ.get("LLM_MAX_CALLS", "200")))
    parser.add_argument("--graph-mode", choices=["full", "incremental"],
                        default=os.environ.get("GRAPH_MODE", "full"),
                        help="图谱写入模式：full 整图重建 / incremental 增量 upsert")
    parser.add_argument("--graph-delete-missing", action="store_true",
                        help="增量模式下删除本轮未见到的节点/边（默认保留）")
    parser.add_argument("--manual-labels", type=Path, default=None,
                        help="手动标签 CSV（object_type,object_id,key,value）")
    parser.add_argument("--list-stages", action="store_true", help="列出阶段及其依赖后退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_file()
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
        graph_mode=args.graph_mode,
        graph_delete_missing=args.graph_delete_missing,
        manual_labels=args.manual_labels,
    )
    llm = None
    if args.llm != "off":
        try:
            # 本机模型慢得多：8B 在 8 GB 显存上约 4 tok/s，60 秒默认会直接超时
            default_timeout = 900.0 if args.llm == "ollama" else 60.0
            timeout_s = (
                args.llm_timeout
                if args.llm_timeout is not None
                else float(os.environ.get("LLM_TIMEOUT_S", default_timeout))
            )
            config = LLMConfig(
                provider=args.llm,
                model=args.llm_model,
                api_key=os.environ.get("DEEPSEEK_API_KEY"),
                timeout_s=timeout_s,
                max_tokens=args.llm_max_tokens,
                num_ctx=(
                    int(os.environ.get("LLM_NUM_CTX", "16384"))
                    if args.llm == "ollama"
                    else None
                ),
                **(
                    {"base_url": args.llm_base_url}
                    if args.llm_base_url
                    else (
                        {"base_url": "http://127.0.0.1:11434"}
                        if args.llm == "ollama"
                        else {}
                    )
                ),
            )
            llm = build_service(
                config,
                settings.output,
                budget=Budget(
                    max_calls=args.llm_max_calls,
                    max_usd=args.llm_budget_usd,
                    max_cny=args.llm_budget_cny,
                    usd_to_cny=args.llm_usd_cny,
                ),
            )
        except LLMUnavailable as exc:
            print(f"无法启用智能体：{exc}", file=sys.stderr)
            return 2

    try:
        pipeline.run(settings, only=stages, llm=llm)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"治理失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - 交互场景
        print("已中断，审计日志与已完成阶段的产物保留。", file=sys.stderr)
        return 130
    return 0
