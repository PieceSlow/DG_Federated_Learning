"""分级复核检测：把"该重评了"的信号写成清单，而不是靠人记得。

每次分级（classify）结束调用 :func:`write_review`，输出
``classification/review_required.json``：

* 新表 / 新列（与上次运行的 schema 快照比）；
* 名称像自由文本/PHI 的新列（note/text/address/name…）→ 建议按 L3 复核；
* 级别变化（来自 level_history 的 diff）。

第一次运行只建立基线，不报"全部是新的"。基线存在
``classification/review_baseline.json``，只存表→列，不含患者数据。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

#: 列名里出现这些词，提示可能是自由文本/直接标识（不是判定，只是"请复核"）
TEXT_HINTS = (
    "note", "text", "comment", "description", "address", "name",
    "ssn", "phone", "email", "free_text",
)


def _text_hint(column: str) -> bool:
    lowered = str(column).lower()
    return any(hint in lowered for hint in TEXT_HINTS)


def detect(snapshot: dict, baseline: dict | None, level_diff: dict | None) -> dict:
    """对比本次 schema 与基线，返回待复核信号。"""
    signals: list[dict] = []
    baseline = baseline or {}
    initial = not baseline

    if not initial:
        for table in sorted(set(snapshot) - set(baseline)):
            signals.append({
                "type": "new_table",
                "table": table,
                "detail": f"新增表 {table}（{snapshot[table].get('rows', '?')} 行），需评估类别/级别",
            })
        for table, info in snapshot.items():
            previous_columns = set((baseline.get(table) or {}).get("columns") or {})
            new_columns = sorted(set(info.get("columns") or {}) - previous_columns)
            for column in new_columns:
                signals.append({
                    "type": "new_column",
                    "table": table,
                    "column": column,
                    "detail": f"{table} 新增列 {column}，需确认是否敏感",
                })
                if _text_hint(column):
                    signals.append({
                        "type": "possible_free_text",
                        "table": table,
                        "column": column,
                        "suggested_level": "L3",
                        "detail": f"{table}.{column} 名称像自由文本/直接标识，建议按 L3 复核",
                    })

    if not initial:
        for key, (old, new) in ((level_diff or {}).get("changed") or {}).items():
            signals.append({
                "type": "level_changed",
                "dataset": key,
                "old": old,
                "new": new,
                "detail": f"{key} 级别 {old}→{new}，需确认变更依据",
            })
        for key in (level_diff or {}).get("added") or []:
            signals.append({
                "type": "level_added",
                "dataset": key,
                "detail": f"{key} 首次纳入分级，需确认",
            })

    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "count": len(signals),
        "signals": signals,
    }


def _columns_only(snapshot: dict) -> dict:
    return {
        table: {"rows": info.get("rows"), "columns": list((info.get("columns") or {}).keys())}
        for table, info in snapshot.items()
    }


def write_review(dest: Path, snapshot: dict, level_diff: dict | None = None) -> dict:
    """写 ``review_required.json`` 并更新基线，返回报告。"""
    dest.mkdir(parents=True, exist_ok=True)
    baseline_path = dest / "review_baseline.json"
    baseline = None
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    report = detect(snapshot, baseline, level_diff)
    (dest / "review_required.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    baseline_path.write_text(
        json.dumps(_columns_only(snapshot), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
