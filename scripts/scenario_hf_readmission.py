"""业务场景闭环示例：心衰（HF）患者 30 天再入院。

这是一个**业务视角**的演示：不碰原始表，只用治理产物
（对象图谱节点 / 患者 360 / KV 标签）回答一个临床科研问题。

业务问题
--------
"在心衰住院患者里，出院后 30 天内再入院的比例是多少？哪些标签和再入院相关？"

对象 → 标签 → 视图
------------------
1. 对象：Encounter（住院，admittime/dischtime）、Diagnosis（HF 诊断码）、Patient（人口学）；
2. 标签：`L4.comorbidity_burden`、`L4.polypharmacy`、`L4.frequent_admitter`、
   `L4.high_followup_priority`（患者级），`L1.sensitivity`（共享边界）；
3. 视图：`hf_readmission_view.csv`，一行一个患者，可直接给临床/随访用。

口径与局限（写在产物里）
------------------------
* HF 定义：ICD-9 428* / ICD-10 I50*（编码级，不是临床确认）；
* 再入院：索引住院出院后，下一次住院的入院时间 ≤ 30 天（只算同患者、按时间排序）；
* 标签阈值是代理口径（见 docs/05），不是临床标准；样本小，只做流程演示。

用法::

    python scripts/scenario_hf_readmission.py --governance output/governance
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

HF_ICD9 = "428"
HF_ICD10 = "I50"


def is_heart_failure(code_system: object, code: object) -> bool:
    system = str(code_system or "").upper()
    value = str(code or "").strip().upper()
    if not value:
        return False
    if system == "ICD9":
        return value.startswith(HF_ICD9)
    if system == "ICD10":
        return value.startswith(HF_ICD10)
    return False


def load_encounters(governance: Path, events: pd.DataFrame | None = None) -> pd.DataFrame:
    """从图谱节点读 Encounter（对象层，不是原始 admissions 表）。

    Encounter 节点属性里没有 subject_id（canonical 对象只存 admittime/dischtime 等），
    所以用事件的 (hadm_id → subject_id) 反查补齐主体，仍然不碰原始表。
    """
    nodes = pd.read_parquet(governance / "graph" / "nodes.parquet")
    frame = nodes[nodes["object_type"] == "Encounter"].copy()
    records = []
    for row in frame.itertuples():
        attributes = json.loads(row.attributes or "{}")
        records.append(
            {
                "hadm_id": str(row.object_id).split(":", 1)[1],
                "subject_id": attributes.get("subject_id"),
                "admittime": attributes.get("admittime"),
                "dischtime": attributes.get("dischtime"),
            }
        )
    encounters = pd.DataFrame(records)
    if encounters.empty:
        return encounters
    if events is not None and not events.empty:
        mapping = (
            events.dropna(subset=["hadm_id", "subject_id"])
            .drop_duplicates("hadm_id")
            .set_index("hadm_id")["subject_id"]
        )
        mapping.index = mapping.index.astype(str)  # 节点里的 hadm_id 是字符串
        encounters["subject_id"] = encounters["hadm_id"].map(mapping)
    encounters["subject_id"] = encounters["subject_id"].astype("Int64")
    encounters["admittime"] = pd.to_datetime(encounters["admittime"], errors="coerce")
    encounters["dischtime"] = pd.to_datetime(encounters["dischtime"], errors="coerce")
    return encounters


def load_patient_labels(governance: Path) -> pd.DataFrame:
    labels = pd.read_parquet(governance / "labels" / "object_labels.parquet")
    patient = labels[labels["object_type"] == "Patient"].copy()
    patient["subject_id"] = patient["object_id"].str.replace("Patient:", "", regex=False)
    patient["subject_id"] = patient["subject_id"].astype("Int64")
    wide = patient.pivot_table(
        index="subject_id", columns="tag_key", values="tag_value", aggfunc="first"
    )
    wide.columns = [str(column).replace(".", "_") for column in wide.columns]
    return wide.reset_index()


def build_view(governance: Path) -> tuple[pd.DataFrame, dict]:
    events = pd.read_parquet(governance / "dataset" / "clinical_events.parquet")
    diagnosis = events[events["event_type"] == "Diagnosis"].copy()
    diagnosis["is_hf"] = [
        is_heart_failure(system, code)
        for system, code in zip(diagnosis["code_system"], diagnosis["code"])
    ]
    hf = diagnosis[diagnosis["is_hf"]]
    patient_360 = pd.read_parquet(governance / "dataset" / "patient_360.parquet")
    encounters = load_encounters(governance, events)
    labels = load_patient_labels(governance)

    hf_hadm = set(hf["hadm_id"].dropna().astype(str))
    rows = []
    for subject_id, group in encounters.groupby("subject_id"):
        group = group.sort_values("admittime")
        hf_index = group[group["hadm_id"].astype(str).isin(hf_hadm)]
        if hf_index.empty:
            continue
        index = hf_index.iloc[0]
        later = group[group["admittime"] > index["dischtime"]]
        readmitted = bool(
            not later.empty
            and (later.iloc[0]["admittime"] - index["dischtime"]).days <= 30
        )
        rows.append(
            {
                "subject_id": int(subject_id),
                "hadm_id": index["hadm_id"],
                "index_admittime": index["admittime"],
                "index_dischtime": index["dischtime"],
                "readmitted_30d": readmitted,
                "days_to_next_admit": (
                    int((later.iloc[0]["admittime"] - index["dischtime"]).days)
                    if not later.empty
                    else None
                ),
            }
        )
    view = pd.DataFrame(rows)
    if view.empty:
        return view, {"cohort": 0}

    view = view.merge(
        patient_360[["subject_id", "gender", "anchor_age", "n_admissions",
                     "total_los_days", "mortality", "split"]],
        on="subject_id", how="left",
    ).merge(labels, on="subject_id", how="left")
    view = view.sort_values(["readmitted_30d", "subject_id"], ascending=[False, True])

    summary = {
        "scenario": "心衰患者 30 天再入院",
        "cohort_patients": int(len(view)),
        "readmitted_30d": int(view["readmitted_30d"].sum()),
        "readmission_rate": round(float(view["readmitted_30d"].mean()), 4),
        "label_coverage": {
            column: int(view[column].notna().sum())
            for column in view.columns if column.startswith("L")
        },
        "notes": [
            "HF 为编码级定义（ICD-9 428*/ICD-10 I50*），不是临床确诊",
            "再入院只统计同患者、索引住院出院后 30 天内的下一次住院",
            "L4 标签为代理口径（docs/05），样本小，仅作流程演示",
        ],
    }
    return view, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="心衰 30 天再入院业务视图")
    parser.add_argument("--governance", type=Path, default=Path("output/governance"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    out_dir = args.out or (args.governance / "scenario")
    out_dir.mkdir(parents=True, exist_ok=True)
    view, summary = build_view(args.governance)
    if view.empty:
        print("没有符合条件的患者（检查图谱是否含 Encounter/Diagnosis）")
        return 1
    view.to_csv(out_dir / "hf_readmission_view.csv", index=False, encoding="utf-8-sig")
    (out_dir / "hf_readmission_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"心衰队列 {summary['cohort_patients']} 人，30 天再入院 "
        f"{summary['readmitted_30d']} 人（{summary['readmission_rate']:.1%}）"
    )
    print(f"视图: {out_dir / 'hf_readmission_view.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
