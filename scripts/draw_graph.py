"""把治理产出的对象图谱画成图（给报告/答辩用）。

两种图：

1. **类型级 schema 图**：8 类对象 + 9 条关系，节点大小=实例数、边标签=关系名与实例数。
   这张图讲"图谱的骨架"，与患者无关，可公开。
2. **患者级实例图**：一个患者 → 他的住院/ICU/事件 → 概念，节点标签用编码/药名。
   这张图讲"图谱具体长什么样"，含患者标识，只用于本地演示。

用法::

    python scripts/draw_graph.py --graph-dir output/governance/graph --out-dir output/graphs
    python scripts/draw_graph.py --graph-dir output_mock10/governance/graph \
        --out-dir output_mock10/graphs --patient 10000032

只依赖 `networkx` + `matplotlib`（requirements 已含 Pillow；这两项在实验环境已安装）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面环境
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
import pandas as pd  # noqa: E402

#: 对象类型 → 颜色（颜色只用于区分类型，不承载敏感级别含义）
TYPE_COLORS = {
    "Patient": "#1f77b4",
    "Encounter": "#2ca02c",
    "ICUStay": "#17becf",
    "ClinicalNote": "#ff7f0e",
    "Diagnosis": "#d62728",
    "Prescription": "#9467bd",
    "Procedure": "#8c564b",
    "Concept": "#7f7f7f",
}

#: 概念边名 → 起点对象类型（manifest 只记录关系计数，不记 from/to）
CONCEPT_EDGE_SOURCE = {
    "diagnosis_concept": "Diagnosis",
    "prescription_concept": "Prescription",
    "procedure_concept": "Procedure",
}


def load_graph(graph_dir: Path) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
    nodes = pd.read_parquet(graph_dir / "nodes.parquet")
    edges = {
        path.stem: pd.read_parquet(path)
        for path in sorted((graph_dir / "edges").glob("*.parquet"))
    }
    manifest = json.loads((graph_dir / "graph_manifest.json").read_text(encoding="utf-8"))
    return nodes, edges, manifest


def _box_height(lines: int) -> float:
    """类框高度：标题 + 每行属性 + 来源脚注。"""
    return 0.52 + 0.24 * lines + 0.40


def draw_class_model(manifest: dict, nodes: pd.DataFrame, out_dir: Path,
                     dpi: int = 180) -> Path:
    """UML 风格类图：对象类型 = 类，字段 = 属性，关系 = 带基数的关联线。"""
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    specs = manifest.get("specs", {})
    counts = {str(k): int(v) for k, v in nodes.groupby("object_type").size().items()}
    # 只画真正实例化了的类（智能体可能漏掉 ClinicalNote，画出来会是 0 实例的空框）
    event_types = [name for name in ("ICUStay", "Diagnosis", "Prescription",
                                     "Procedure", "ClinicalNote")
                   if name in specs and counts.get(name, 0) > 0]

    # 每个类最多展示 9 行字段，其余折成 "+N more"，避免类框高度失控
    shown: dict[str, list[str]] = {}
    hidden: dict[str, int] = {}
    for name, spec in specs.items():
        keys = list(spec.get("keys", []))
        attrs = [attr for attr in spec.get("attributes", []) if attr not in keys]
        limit = 9
        picked = keys[:limit] + attrs[: max(0, limit - len(keys))]
        shown[name] = picked
        hidden[name] = len(keys) + len(attrs) - len(picked)

    heights = {
        name: 0.60 + 0.24 * (len(shown[name]) + (1 if hidden[name] else 0)) + 0.34
        for name in specs
    }

    event_max_h = max((heights[name] for name in event_types), default=2.0)
    event_top = 5.2 + event_max_h
    bus_y = event_top + 0.45
    concept_y = event_top - event_max_h - 0.55 - heights.get("Concept", 2.0) / 2
    encounter_y = bus_y + 0.55 + heights["Encounter"] / 2 if "Encounter" in specs else event_top
    patient_y = (encounter_y + heights["Encounter"] / 2 + 0.55 + heights["Patient"] / 2
                 if "Encounter" in specs and "Patient" in specs else encounter_y + 2)

    event_xs = [1.9 + 3.35 * index for index in range(len(event_types))]
    positions = {}
    if "Patient" in specs and counts.get("Patient", 0) > 0:
        positions["Patient"] = (8.6, patient_y)
    if "Encounter" in specs and counts.get("Encounter", 0) > 0:
        positions["Encounter"] = (8.6, encounter_y)
    for x, name in zip(event_xs, event_types):
        positions[name] = (x, event_top - heights[name] / 2)
    if "Concept" in specs and counts.get("Concept", 0) > 0:
        positions["Concept"] = (8.6, concept_y)

    widths = {name: 3.3 for name in positions}
    y_low = min(concept_y - heights.get("Concept", 2) / 2, min(positions[n][1] - heights[n] / 2 for n in positions)) - 0.4
    y_high = max(positions[n][1] + heights[n] / 2 for n in positions) + 0.4
    figure, axis = plt.subplots(figsize=(17.5, max(8.0, 0.62 * (y_high - y_low))))
    axis.set_xlim(0, 17.4)
    axis.set_ylim(y_low, y_high)
    axis.axis("off")

    def top(name: str) -> float:
        return positions[name][1] + heights[name] / 2

    def bottom(name: str) -> float:
        return positions[name][1] - heights[name] / 2

    for name in positions:
        x, y = positions[name]
        width, height = widths[name], heights[name]
        spec = specs[name]
        color = TYPE_COLORS.get(name, "#999999")
        axis.add_patch(FancyBboxPatch(
            (x - width / 2, y - height / 2), width, height,
            boxstyle="round,pad=0.02,rounding_size=0.08",
            linewidth=1.4, edgecolor="#444444", facecolor="white", zorder=3))
        axis.add_patch(FancyBboxPatch(
            (x - width / 2, y + height / 2 - 0.46), width, 0.46,
            boxstyle="round,pad=0.02,rounding_size=0.08",
            linewidth=0, facecolor=color, alpha=0.30, zorder=4))
        axis.text(x - width / 2 + 0.14, y + height / 2 - 0.23, name,
                  fontsize=10.5, fontweight="bold", va="center", zorder=5)
        axis.text(x + width / 2 - 0.14, y + height / 2 - 0.23,
                  str(spec.get("sensitivity", "")), fontsize=8.5, va="center",
                  ha="right", zorder=5)
        axis.plot([x - width / 2, x + width / 2], [y + height / 2 - 0.46] * 2,
                  color="#666666", linewidth=0.9, zorder=4)

        line_y = y + height / 2 - 0.46 - 0.18
        key_set = set(spec.get("keys", []))
        for name_field in shown[name]:
            prefix = "+" if name_field in key_set else "-"
            axis.text(x - width / 2 + 0.14, line_y, f"{prefix} {name_field}", fontsize=8.2,
                      va="center", zorder=5, family="DejaVu Sans")
            line_y -= 0.24
        if hidden[name]:
            axis.text(x - width / 2 + 0.14, line_y, f"… +{hidden[name]} more",
                      fontsize=7.6, va="center", color="#777777", style="italic", zorder=5)
            line_y -= 0.24
        footer = f"source: {spec.get('source_table', '?')} · {counts.get(name, 0):,}"
        axis.text(x - width / 2 + 0.14, y - height / 2 + 0.15, footer, fontsize=6.8,
                  color="#666666", va="center", style="italic", zorder=5,
                  family="DejaVu Sans")

    if event_types:
        left = min(event_xs) - widths[event_types[0]] / 2 - 0.2
        right = max(event_xs) + widths[event_types[-1]] / 2 + 0.2
        event_tops = max(top(name) for name in event_types) + 0.2
        event_bottoms = min(bottom(name) for name in event_types) - 0.2
        axis.add_patch(FancyBboxPatch(
            (left, event_bottoms), right - left, event_tops - event_bottoms,
            boxstyle="round,pad=0.02,rounding_size=0.12", linewidth=1.0,
            edgecolor="#999999", facecolor="none", linestyle="--", zorder=1))
        axis.text(left + 0.1, event_tops + 0.03, "event layer", fontsize=8,
                  color="#777777", va="bottom", style="italic")

    def arrow(x1: float, y1: float, x2: float, y2: float) -> None:
        axis.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                       mutation_scale=12, linewidth=1.2,
                                       color="#555555", zorder=2))

    if "Patient" in specs and "Encounter" in specs:
        arrow(8.6, bottom("Patient"), 8.6, top("Encounter"))
        axis.text(8.78, (bottom("Patient") + top("Encounter")) / 2, "has_encounter  1:N",
                  fontsize=7.5, color="#333333", ha="left", va="center",
                  bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.85})

    if "Encounter" in specs:
        axis.plot([8.6, 8.6], [bottom("Encounter"), bus_y], color="#555555",
                  linewidth=1.2, zorder=2)
        axis.plot([min(event_xs), max(event_xs)], [bus_y, bus_y], color="#555555",
                  linewidth=1.2, zorder=2)
        relation_by_target = {spec["to"]: spec["name"]
                              for spec in manifest.get("relation_specs", [])
                              if spec.get("from") == "Encounter"}
        for x, name in zip(event_xs, event_types):
            arrow(x, bus_y, x, top(name))
            relation = relation_by_target.get(name, f"has_{name.lower()}")
            axis.text(x + 0.06, bus_y + 0.06, f"{relation}  1:N", fontsize=6.8,
                      color="#333333", ha="left", va="bottom", zorder=6,
                      bbox={"boxstyle": "round,pad=0.10", "fc": "white", "ec": "none", "alpha": 0.9})

    if "Concept" in specs:
        concept_x, concept_top = positions["Concept"][0], top("Concept")
        for name, relation in (("Diagnosis", "diagnosis_concept"),
                               ("Prescription", "prescription_concept"),
                               ("Procedure", "procedure_concept")):
            if name not in specs:
                continue
            x = positions[name][0]
            arrow(x, bottom(name), concept_x, concept_top)
            axis.text(x + 0.08, bottom(name) - 0.14, f"{relation}  N:1", fontsize=6.8,
                      color="#333333", ha="left", va="top", zorder=6,
                      bbox={"boxstyle": "round,pad=0.10", "fc": "white", "ec": "none", "alpha": 0.9})

    relation_count = len(manifest.get("relations", {}))
    axis.set_title(
        f"Object model — UML class diagram: {len(positions)} classes, {relation_count} associations\n"
        "+ key field   - attribute   (sensitivity tag in title bar; instance count in footer)",
        fontsize=13, pad=10)
    figure.tight_layout()
    out_path = out_dir / "graph_classes.png"
    figure.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return out_path


def _schema_positions(types: list[str]) -> dict[str, tuple[float, float]]:
    """固定布局：Patient → Encounter → 事件类型 → Concept，像一张"骨架示意图"。"""
    import math

    positions: dict[str, tuple[float, float]] = {}
    if "Patient" in types:
        positions["Patient"] = (0.0, 0.0)
    if "Encounter" in types:
        positions["Encounter"] = (0.0, -1.15)
    events = [t for t in types if t not in {"Patient", "Encounter", "Concept"}]
    count = max(len(events), 1)
    for index, object_type in enumerate(events):
        # 事件类型铺在 Encounter 下方的半圆上，避免节点重叠
        angle = math.pi * (1.0 + (index + 0.5) / count)
        positions[object_type] = (1.9 * math.cos(angle), -1.15 + 1.9 * math.sin(angle))
    if "Concept" in types:
        # 只有一类事件时把概念挪到侧边，别和事件连成一条竖线
        positions["Concept"] = (1.35, -3.0) if len(events) == 1 else (0.0, -3.45)
    return positions


def draw_schema(nodes: pd.DataFrame, edges: dict[str, pd.DataFrame], manifest: dict,
                out_dir: Path, dpi: int = 180) -> Path:
    """类型级 schema 图：8 类对象 + 9 条关系，节点标类型与实例数。"""
    counts = {str(k): int(v) for k, v in nodes.groupby("object_type").size().items()}
    graph = nx.MultiDiGraph()
    for object_type, count in counts.items():
        graph.add_node(object_type, count=count)

    specs = {spec["name"]: spec for spec in manifest.get("relation_specs", [])}
    for relation, frame in edges.items():
        if relation in specs:
            source = specs[relation]["from"]
            target = specs[relation]["to"]
        elif relation in CONCEPT_EDGE_SOURCE:
            source, target = CONCEPT_EDGE_SOURCE[relation], "Concept"
        else:
            continue
        if source in counts and target in counts:
            graph.add_edge(source, target, relation=relation, weight=len(frame))

    positions = _schema_positions(list(graph.nodes))
    figure, axis = plt.subplots(figsize=(12, 8.5))
    fixed_size = {"Patient": 2600, "Encounter": 1900, "Concept": 1400, "ICUStay": 1300,
                  "ClinicalNote": 1300, "Diagnosis": 1500, "Prescription": 1500,
                  "Procedure": 1300}
    sizes = [fixed_size.get(node, 1200) for node in graph.nodes]
    colors = [TYPE_COLORS.get(node, "#999999") for node in graph.nodes]
    nx.draw_networkx_nodes(graph, positions, ax=axis, node_size=sizes, node_color=colors,
                           alpha=0.92, edgecolors="white", linewidths=2.5)
    nx.draw_networkx_edges(graph, positions, ax=axis, arrows=True, arrowstyle="-|>",
                           arrowsize=20, width=1.6, edge_color="#8c8c8c",
                           connectionstyle="arc3,rad=0.06", node_size=sizes,
                           min_source_margin=18, min_target_margin=18)
    labels = {node: f"{node}\n{counts.get(node, 0):,}" for node in graph.nodes}
    nx.draw_networkx_labels(graph, positions, labels=labels, font_size=10.5,
                            font_family="DejaVu Sans", font_weight="bold")
    edge_labels = {(u, v): f"{data['relation']} ({data['weight']:,})"
                   for u, v, data in graph.edges(data=True)}
    nx.draw_networkx_edge_labels(graph, positions, edge_labels=edge_labels, font_size=8,
                                 font_family="DejaVu Sans", label_pos=0.5,
                                 bbox={"boxstyle": "round,pad=0.18", "fc": "white",
                                       "ec": "none", "alpha": 0.85})
    axis.set_title(
        f"Object graph schema — {len(counts)} object types, {graph.number_of_edges()} relations\n"
        f"node label = type + instance count (total {sum(counts.values()):,} nodes)",
        fontsize=13,
    )
    axis.axis("off")
    axis.margins(0.12)
    figure.tight_layout()
    out_path = out_dir / "graph_schema.png"
    figure.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(figure)

    dot_path = out_dir / "graph_schema.dot"
    lines = ["digraph object_graph {", "  rankdir=TB;", "  node [shape=box, style=rounded];"]
    for node in graph.nodes:
        lines.append(f'  "{node}" [label="{node}\\n{counts.get(node, 0)}"];')
    for u, v, data in graph.edges(data=True):
        lines.append(f'  "{u}" -> "{v}" [label="{data["relation"]}"];')
    lines.append("}")
    dot_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _short_label(object_type: str, attributes: dict) -> str:
    if object_type == "Diagnosis":
        return str(attributes.get("icd_code", ""))[:12]
    if object_type == "Procedure":
        return str(attributes.get("icd_code", ""))[:12]
    if object_type == "Prescription":
        return str(attributes.get("formulary_drug_cd") or attributes.get("drug", ""))[:16]
    if object_type == "ClinicalNote":
        return "note"
    if object_type == "ICUStay":
        return "ICU"
    if object_type == "Concept":
        return str(attributes.get("cui") or "")[:10]
    return object_type[:8]


def _choose_patient(edges: dict[str, pd.DataFrame]) -> str | None:
    """挑一个"好看"的患者：优先 1 次住院、事件数适中，否则取事件最少的。"""
    encounter_edges = edges.get("has_encounter")
    if encounter_edges is None or encounter_edges.empty:
        return None
    encounter_of = dict(zip(encounter_edges["dst_id"], encounter_edges["src_id"]))
    event_counts: dict[str, int] = {}
    for name, frame in edges.items():
        if not name.startswith("has_") or name in {"has_encounter", "has_icu_stay"}:
            continue
        for src in frame["src_id"]:  # 事件边的 src 是 Encounter
            patient = encounter_of.get(src)
            if patient:
                event_counts[patient] = event_counts.get(patient, 0) + 1
    if not event_counts:
        return None
    encounter_count = encounter_edges.groupby("src_id").size().to_dict()
    candidates = [
        (patient, count)
        for patient, count in event_counts.items()
        if encounter_count.get(patient, 0) == 1 and 5 <= count <= 30
    ]
    if candidates:
        return sorted(candidates, key=lambda item: item[1])[0][0]
    return min(event_counts, key=event_counts.get)


def draw_patient(nodes: pd.DataFrame, edges: dict[str, pd.DataFrame], patient: str,
                 out_dir: Path, max_events: int = 18, dpi: int = 200) -> Path:
    """患者级实例图：一行一个事件，右边挂它的概念（行列布局，避免毛线球）。"""
    graphs = {name: nx.from_pandas_edgelist(frame, "src_id", "dst_id", create_using=nx.DiGraph)
              for name, frame in edges.items()}
    patient_id = patient if patient.startswith("Patient:") else f"Patient:{patient}"

    encounter_graph = graphs.get("has_encounter")
    encounters = (list(encounter_graph.successors(patient_id))
                  if encounter_graph is not None and patient_id in encounter_graph else [])

    event_edges = [name for name in graphs
                   if name.startswith("has_") and name not in {"has_encounter", "has_icu_stay"}]
    events: list[str] = []
    for name in event_edges:
        graph = graphs[name]
        for encounter in encounters:
            if encounter in graph:
                events.extend(graph.successors(encounter))
    events = sorted(events)[:max_events]

    concept_edges = [name for name in graphs if name.endswith("_concept")]
    concepts_of: dict[str, list[str]] = {}
    for name in concept_edges:
        graph = graphs[name]
        for event in events:
            if event in graph:
                concepts_of[event] = list(graph.successors(event))[:3]

    node_type = dict(zip(nodes["object_id"], nodes["object_type"]))
    attributes_by_id = {row.object_id: json.loads(row.attributes or "{}")
                        for row in nodes.itertuples()}

    # ---- 布局：Patient / Encounter 在左，事件按行排，概念挂在事件右侧 ----
    positions: dict[str, tuple[float, float]] = {patient_id: (0.0, 0.0)}
    graph = nx.DiGraph()
    graph.add_node(patient_id)
    encounter_y = {enc: index * 3.0 for index, enc in enumerate(encounters)}
    for enc, y in encounter_y.items():
        positions[enc] = (1.2, y)
        graph.add_node(enc)
        graph.add_edge(patient_id, enc)
    for row, event in enumerate(events):
        positions[event] = (2.4, float(row))
        graph.add_node(event)
        # 找到事件所属的 encounter
        for name in event_edges:
            frame = edges[name]
            match = frame[frame["dst_id"] == event]
            if not match.empty:
                source = str(match["src_id"].iat[0])
                if source in positions:
                    graph.add_edge(source, event)
                break
        for index, concept in enumerate(concepts_of.get(event, [])):
            if concept not in positions:
                positions[concept] = (3.35 + 0.62 * index, float(row))
                graph.add_node(concept)
            graph.add_edge(event, concept)

    max_row = max(len(events), 1)
    figure, axis = plt.subplots(figsize=(13, max(6.5, 0.34 * max_row + 2)))
    sizes = [{"Patient": 1400, "Encounter": 700, "Concept": 60}.get(node_type.get(n, ""), 260)
             for n in graph.nodes]
    colors = [TYPE_COLORS.get(node_type.get(n, ""), "#999999") for n in graph.nodes]
    nx.draw_networkx_nodes(graph, positions, ax=axis, node_size=sizes, node_color=colors,
                           alpha=0.9, edgecolors="white", linewidths=1.0)
    nx.draw_networkx_edges(graph, positions, ax=axis, arrows=True, arrowstyle="-|>",
                           arrowsize=8, width=0.6, edge_color="#c8c8c8")
    labels: dict[str, str] = {}
    for node in graph.nodes:
        object_type = node_type.get(node, "")
        if object_type == "Patient":
            labels[node] = patient_id
        elif object_type == "Encounter":
            labels[node] = "Encounter " + node.replace("Encounter:", "")
        elif object_type == "Concept":
            labels[node] = str(attributes_by_id.get(node, {}).get("cui") or "")[:10]
        else:
            labels[node] = f"{object_type}: {_short_label(object_type, attributes_by_id.get(node, {}))}"
    nx.draw_networkx_labels(graph, positions, labels=labels, font_size=7.5,
                            font_family="DejaVu Sans")
    axis.set_title(
        f"{patient_id} — {len(encounters)} encounter(s), {len(events)} events, "
        f"{sum(len(v) for v in concepts_of.values())} event→concept links\n"
        "rows: events (with code); right: linked concepts (CUI); left: patient/encounter",
        fontsize=11,
    )
    axis.axis("off")
    axis.margins(0.05)
    figure.tight_layout()
    out_path = out_dir / f"graph_{patient_id.replace(':', '_')}.png"
    figure.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(figure)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="画对象图谱（类型级 + 患者级）")
    parser.add_argument("--graph-dir", type=Path,
                        default=Path("output/governance/graph"), help="图谱产物目录")
    parser.add_argument("--out-dir", type=Path, default=None, help="图片输出目录")
    parser.add_argument("--patient", default=None, help="患者 id（默认取事件最多的患者）")
    parser.add_argument("--max-events", type=int, default=18, help="患者级实例图最多画多少事件")
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args(argv)

    graph_dir = args.graph_dir
    out_dir = args.out_dir or graph_dir.parent / "graphs"
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes, edges, manifest = load_graph(graph_dir)
    schema_path = draw_schema(nodes, edges, manifest, out_dir, dpi=args.dpi)
    print(f"类型级 schema 图: {schema_path}")
    class_path = draw_class_model(manifest, nodes, out_dir, dpi=args.dpi)
    print(f"UML 类图: {class_path}")

    patient = args.patient or _choose_patient(edges)
    if patient:
        patient_path = draw_patient(nodes, edges, patient, out_dir,
                                    max_events=args.max_events, dpi=args.dpi)
        print(f"患者级实例图: {patient_path}")
    else:
        print("没有 encounter 边，跳过患者级实例图")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
