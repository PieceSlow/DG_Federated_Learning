"""治理运行上下文。

一次运行只持有一个 ``GovernanceContext``：各阶段读写它，而不是互相传十几个参数。
好处是审计日志、问题清单、统计口径天然统一——"哪一阶段、处理了多少、产生几条告警"
都能在同一个对象里追溯。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from governance.config import Settings

#: 同一规则最多保留多少条明细（计数不截断），避免问题文件无限膨胀
ISSUE_CAP_PER_RULE = 200


@dataclass
class GovernanceContext:
    settings: Settings

    # ---- 输入侧 ----
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    cohort: list[int] = field(default_factory=list)
    inventory: list[dict[str, Any]] = field(default_factory=list)

    # ---- 加工侧 ----
    objects: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    clinical_events: pd.DataFrame | None = None
    patient_360: pd.DataFrame | None = None
    concept_map: pd.DataFrame | None = None
    record_map: pd.DataFrame | None = None
    field_map: pd.DataFrame | None = None
    object_graph: dict[str, Any] | None = None
    labels: dict[str, Any] | None = None
    catalog: dict[str, Any] | None = None

    # ---- 质量与审计 ----
    issues: list[dict[str, Any]] = field(default_factory=list)
    issue_counts: dict[str, int] = field(default_factory=dict)
    validators: list[str] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    stage_timings: list[tuple[str, float]] = field(default_factory=list)

    def log(self, stage: str, message: str) -> None:
        self.audit.append(
            {"stage": stage, "message": message, "at": datetime.now(timezone.utc).isoformat()}
        )

    def add_issue(
        self,
        rule: str,
        severity: str,
        source: str,
        detail: str,
        subject: int | None = None,
        record: str | None = None,
    ) -> None:
        key = f"{rule}|{severity}"
        self.issue_counts[key] = self.issue_counts.get(key, 0) + 1
        if self.issue_counts[key] <= ISSUE_CAP_PER_RULE:
            self.issues.append(
                {
                    "rule": rule,
                    "severity": severity,
                    "source_table": source,
                    "source_record_id": record,
                    "subject_id": subject,
                    "detail": detail,
                }
            )

    def issues_frame(self) -> pd.DataFrame:
        columns = ["rule", "severity", "source_table", "source_record_id", "subject_id", "detail"]
        return pd.DataFrame(self.issues, columns=columns)

    def write_audit(self, path: Path) -> None:
        pd.DataFrame(self.audit).to_csv(path, index=False, encoding="utf-8-sig")


def _is_empty(value: object) -> bool:
    """判断前置结果是否为空；DataFrame 不能用 ``in``/``==`` 比较。"""
    if value is None:
        return True
    if isinstance(value, pd.DataFrame):
        return value.empty
    if isinstance(value, (dict, list, tuple, set)):
        return len(value) == 0
    return False


def require_state(ctx: GovernanceContext, *attrs: str) -> None:
    """校验阶段前置状态，缺什么就直说缺什么。"""
    missing = [attr for attr in attrs if _is_empty(getattr(ctx, attr, None))]
    if missing:
        raise RuntimeError(
            "缺少前置结果: " + ", ".join(missing) + "；请先运行更早的阶段（见 run.py --list-stages）"
        )
