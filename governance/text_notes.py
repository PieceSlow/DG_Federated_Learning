"""从事件流还原"文书对象"表（note_id/subject_id/hadm_id/charttime）。"""

from __future__ import annotations

import pandas as pd

from governance.context import GovernanceContext


def note_frame(ctx: GovernanceContext) -> pd.DataFrame:
    rows: list[dict] = []
    seen: set[str] = set()
    for event in ctx.events:
        if event.get("code") != "NOTE//DISCHARGE":
            continue
        record = str(event.get("source_record_id") or "")
        note_id = record.split(":", 1)[1] if ":" in record else record
        if not note_id or note_id in seen:
            continue
        seen.add(note_id)
        rows.append(
            {
                "note_id": note_id,
                "subject_id": event.get("subject_id"),
                "hadm_id": event.get("hadm_id"),
                "charttime": event.get("time"),
                "note_type": "DS",
            }
        )
    return pd.DataFrame(
        rows, columns=["note_id", "subject_id", "hadm_id", "charttime", "note_type"]
    )
