"""动态新增对象：把「智能体/人工提出的对象」校验后写入 ``objects.local.yaml``。

流程（对应"能更新"里的模式层更新）::

    新表出现 → ObjectFinder/人提出 spec → 本脚本校验（表/列/主键/敏感级）
             → 人工确认（--apply --yes）→ 写入 overlay
             → 下次 run.py 的 load_model 自动带上（不覆盖 objects.yaml）

用法::

    # 1) 预览（不写文件）
    python scripts/propose_object.py --table labevents --name LabEvent \\
        --keys labevent_id --attributes itemid,charttime,valuenum,valueuom,flag \\
        --sensitivity L2 --concept "一次检验结果" \\
        --relation-name has_lab_event --relation-from Encounter --relation-on hadm_id

    # 2) 确认写入
    ... --apply --yes

    # 3) 从智能体提案 JSON 里取（objects/relations 数组）
    python scripts/propose_object.py --from-proposal graph/agent_proposal.json --table labevents --apply --yes

注意：overlay 只追加，不覆盖；同名对象/关系直接报错，避免"悄悄改模型"。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

# 允许 `python scripts/propose_object.py` 直接运行（仓库根加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from governance.graph.model import RelationSpec, load_model  # noqa: E402

SENSITIVITY = {"L1", "L2", "L3"}


def load_snapshot(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"缺少表结构快照 {path}；先跑一次流水线（ingest 阶段会写 schema_snapshot.json）"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def validate(
    snapshot: dict,
    base_objects: dict,
    table: str,
    name: str,
    keys: list[str],
    sensitivity: str,
    relation: dict | None,
) -> list[str]:
    errors: list[str] = []
    if table not in snapshot:
        errors.append(f"表 {table} 不在本次运行的表里（schema_snapshot）")
    else:
        columns = set(snapshot[table]["columns"])
        missing = [key for key in keys if key not in columns]
        if missing:
            errors.append(f"主键不在 {table} 的列里: {', '.join(missing)}")
    if not keys:
        errors.append("主键不能为空")
    if name in base_objects:
        errors.append(f"对象 {name} 已存在，overlay 不覆盖")
    if sensitivity not in SENSITIVITY:
        errors.append(f"sensitivity 必须是 {sorted(SENSITIVITY)}")
    if relation:
        source = relation.get("from")
        on = relation.get("on")
        if source not in base_objects and source != name:
            errors.append(f"关系起点 {source} 不是已有对象（也不是本对象）")
        elif source != name and on not in base_objects[source].keys:
            errors.append(f"关系 on={on} 必须是 {source} 的主键")
        if table in snapshot and on not in snapshot[table]["columns"]:
            errors.append(f"关系 on={on} 不在 {table} 的列里")
    return errors


def objective_spec(args) -> tuple[dict, dict | None]:
    keys = [item.strip() for item in (args.keys or "").split(",") if item.strip()]
    attributes = [item.strip() for item in (args.attributes or "").split(",") if item.strip()]
    spec = {
        "name": args.name,
        "source_table": args.table,
        "keys": keys,
        "attributes": attributes,
        "sensitivity": args.sensitivity,
        "concept": args.concept or args.name,
        "interface": args.table,
    }
    relation = None
    if args.relation_name:
        relation = {
            "name": args.relation_name,
            "from": args.relation_from,
            "to": spec["name"],
            "on": args.relation_on,
            "cardinality": args.cardinality,
        }
    return spec, relation


def spec_from_proposal(proposal_path: Path, table: str) -> tuple[dict, dict | None]:
    data = json.loads(proposal_path.read_text(encoding="utf-8"))
    candidates = [item for item in data.get("objects", []) if item.get("source_table") == table]
    if not candidates:
        raise SystemExit(f"提案里没有 source_table={table} 的对象")
    item = candidates[0]
    spec = {
        "name": item["name"],
        "source_table": table,
        "keys": item.get("keys") or [],
        "attributes": item.get("attributes") or [],
        "sensitivity": item.get("sensitivity") or "L2",
        "concept": item.get("reason") or item["name"],
        "interface": table,
    }
    relations = [
        relation for relation in data.get("relations", [])
        if relation.get("to") == item["name"]
    ]
    relation = relations[0] if relations else None
    return spec, relation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="动态新增对象（写入 objects.local.yaml）")
    parser.add_argument("--snapshot", type=Path,
                        default=Path("output/governance/metadata/schema_snapshot.json"))
    parser.add_argument("--overlay", type=Path,
                        default=Path("governance/graph/objects.local.yaml"))
    parser.add_argument("--from-proposal", type=Path, default=None,
                        help="智能体提案 JSON（objects/relations）")
    parser.add_argument("--table", required=False)
    parser.add_argument("--name", required=False)
    parser.add_argument("--keys", default="")
    parser.add_argument("--attributes", default="")
    parser.add_argument("--sensitivity", default="L2")
    parser.add_argument("--concept", default="")
    parser.add_argument("--relation-name", default="")
    parser.add_argument("--relation-from", default="Encounter")
    parser.add_argument("--relation-on", default="hadm_id")
    parser.add_argument("--cardinality", default="1:N")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true", help="确认写入（与 --apply 一起用）")
    args = parser.parse_args(argv)

    snapshot = load_snapshot(args.snapshot)
    base = load_model().objects

    if args.from_proposal:
        if not args.table:
            raise SystemExit("--from-proposal 需要同时给 --table")
        spec, relation = spec_from_proposal(args.from_proposal, args.table)
    else:
        if not (args.table and args.name):
            raise SystemExit("要么给 --table/--name，要么给 --from-proposal")
        spec, relation = objective_spec(args)

    errors = validate(snapshot, base, spec["source_table"], spec["name"],
                      spec["keys"], spec["sensitivity"], relation)
    print(json.dumps({"objects": [spec], "relations": [relation] if relation else []},
                     ensure_ascii=False, indent=2))
    if errors:
        print("\n校验失败：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    if not args.apply:
        print("\n预览通过。确认写入请加 --apply --yes")
        return 0
    if not args.yes:
        print("需要 --yes 才写入（防止误改模型）", file=sys.stderr)
        return 1

    overlay = {}
    if args.overlay.exists():
        overlay = yaml.safe_load(args.overlay.read_text(encoding="utf-8")) or {}
    overlay.setdefault("objects", []).append(spec)
    if relation:
        overlay.setdefault("relations", []).append(relation)
    args.overlay.parent.mkdir(parents=True, exist_ok=True)
    args.overlay.write_text(
        yaml.safe_dump(overlay, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"\n已写入 {args.overlay}；下次 run.py 会自动带上 {spec['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
