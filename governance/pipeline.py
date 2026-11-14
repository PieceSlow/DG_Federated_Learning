"""治理流水线编排（LangGraph StateGraph）。

11 个阶段各是一个节点，共享同一个 :class:`GovernanceContext`；依赖关系在节点的
``requires`` 里声明，运行时先校验再执行，缺前置时直接说明缺什么。

```text
01 数据接入 → 02 组织与标准化 → 03 质量校验 → 04 事件仓储
   → 05 多源汇聚 → 06 字段对齐 → 07 对象化 → 08 标签化
   → 09 分类分级 → 10 编目 → 11 质量报告
```

用图而不是一个 ``main()``：任意阶段可单独重跑（``--stages``），每个节点的输入输出
与耗时都留在审计日志里，出问题时能定位到"哪一段、处理了多少、产生几条告警"。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, TypedDict

from governance import (
    align,
    assemble,
    catalog,
    classify,
    ingest,
    label,
    materialize,
    objectify,
    quality,
    report,
    standardize,
)
from governance.config import Settings
from governance.context import GovernanceContext, require_state


@dataclass(frozen=True)
class Stage:
    key: str
    title: str
    summary: str
    run: Callable[[GovernanceContext], None]
    requires: tuple[str, ...] = ()


STAGES: tuple[Stage, ...] = (
    Stage("ingest", "01 数据接入", "读取源表、确定确定性队列、登记数据源清单", ingest.run),
    Stage("standardize", "02 组织与标准化", "对象索引、跨表关联、事件映射、文本抽取", standardize.run,
          ("tables", "cohort")),
    Stage("quality", "03 质量校验", "编码/关联/时间/重复校验 + MEDS 核心 schema", quality.run, ("events",)),
    Stage("materialize", "04 事件仓储落盘", "写 MEDS 事件仓储并读回复验", materialize.run, ("events",)),
    Stage("assemble", "05 多源汇聚", "统一事件表 + 患者 360 宽表", assemble.run, ("tables", "events")),
    Stage("align", "06 字段对齐", "字段级与记录级映射、血缘", align.run, ("clinical_events", "patient_360")),
    Stage("objectify", "07 对象化", "对象模型、关系与对象图谱", objectify.run, ("tables",)),
    Stage("label", "08 标签化", "对象/术语/章节/敏感级别/划分五类标签", label.run, ("clinical_events",)),
    Stage("classify", "09 分类分级", "分级规则与落地结果", classify.run, ("clinical_events", "patient_360")),
    Stage("catalog", "10 编目", "资源/资产/服务/共享/开放五级目录", catalog.run,
          ("clinical_events", "patient_360")),
    Stage("report", "11 质量报告", "事件流与治理后数据集的质量汇总", report.run,
          ("clinical_events", "patient_360")),
)

STAGE_BY_KEY = {stage.key: stage for stage in STAGES}


class PipelineState(TypedDict, total=False):
    ctx: GovernanceContext


def describe_stages() -> str:
    lines = ["阶段顺序：", ""]
    for index, stage in enumerate(STAGES, start=1):
        requires = "、".join(stage.requires) if stage.requires else "—"
        lines.append(f"  {index:>2}. {stage.title:<10s} {stage.key:<12s} 依赖: {requires}")
        lines.append(f"      {stage.summary}")
    return "\n".join(lines)


def _select(only: list[str] | None) -> tuple[Stage, ...]:
    if not only:
        return STAGES
    unknown = [key for key in only if key not in STAGE_BY_KEY]
    if unknown:
        raise ValueError(
            f"未知阶段: {', '.join(unknown)}；可用阶段: {', '.join(STAGE_BY_KEY)}"
        )
    wanted = set(only)
    return tuple(stage for stage in STAGES if stage.key in wanted)


def _make_node(stage: Stage):
    def node(state: PipelineState) -> PipelineState:
        ctx = state["ctx"]
        require_state(ctx, *stage.requires)
        started = time.perf_counter()
        stage.run(ctx)
        elapsed = time.perf_counter() - started
        ctx.stage_timings.append((stage.key, round(elapsed, 2)))
        print(f"[{stage.title}] 完成，用时 {elapsed:.1f}s")
        return {"ctx": ctx}

    return node


def build_graph(selected: tuple[Stage, ...]):
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可操作提示
        raise ImportError(
            "缺少依赖 langgraph，请先执行：python -m pip install -r requirements.txt"
        ) from exc

    graph = StateGraph(PipelineState)
    for stage in selected:
        graph.add_node(stage.key, _make_node(stage))
    graph.add_edge(START, selected[0].key)
    for current, following in zip(selected, selected[1:]):
        graph.add_edge(current.key, following.key)
    graph.add_edge(selected[-1].key, END)
    return graph.compile()


def run(settings: Settings, only: list[str] | None = None) -> GovernanceContext:
    settings.ensure_dirs()
    selected = _select(only)
    if not selected:
        raise ValueError("没有选中任何阶段")

    ctx = GovernanceContext(settings)
    started = time.perf_counter()
    print(f"治理流水线开始：{len(selected)} 个阶段，输出目录 {settings.output}")
    try:
        build_graph(selected).invoke({"ctx": ctx})
    finally:
        ctx.write_audit(settings.audit_dir / "audit_log.csv")
        _write_run_summary(ctx, time.perf_counter() - started)
    return ctx


def _write_run_summary(ctx: GovernanceContext, total_seconds: float) -> None:
    summary = {
        "engine": "LangGraph StateGraph",
        "stages": [{"key": key, "seconds": seconds} for key, seconds in ctx.stage_timings],
        "total_seconds": round(total_seconds, 2),
        "cohort_size": int(ctx.stats.get("cohort_size", 0)),
        "artifacts": {
            "eventstore": str(ctx.settings.eventstore),
            "governance": str(ctx.settings.governance),
            "audit": str(ctx.settings.audit_dir / "audit_log.csv"),
        },
        "counts": {
            "events": int(ctx.stats.get("eventstore_rows", 0)),
            "clinical_events": int(ctx.stats.get("clinical_events_rows", 0)),
            "patients": int(ctx.stats.get("cohort_size", 0)),
        },
    }
    (ctx.settings.output / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
