# alignment 包状态

## 目标

- `target_schema.yaml`：对象字段目标（只列**由源字段直接提供**的 8 个字段，并限定允许的源表）；
- `meds_targets.yaml`：MEDS 接口目标（9 类事件 × 核心 5 列），用于接口交叉表。

## 匹配流程

```text
extract_source_fields（类型/空值率/基数）
  × load_object_targets（目标字段 + 允许源表）
  → matchers（name / type / structure / value）
  → combine（0.45/0.25/0.15/0.15）
  → select（top-3 + 阈值 0.5 + 类型过滤，允许 1:N）
  → field_level.csv（auto/review + evidence）
  → evaluate.coverage（覆盖率，无 gold）
```

## 文件

| 文件 | 作用 |
|---|---|
| `target_schema.yaml` / `target_schema.py` | 对象字段目标与加载 |
| `matchers.py` | 四个匹配器 |
| `combine.py` | 加权组合 |
| `select.py` | 候选 top-k 与类型过滤 |
| `evaluate.py` | 覆盖率（目标/必填/auto/review），不再计算 gold P/R |
| `crosswalk.py` | MEDS 9×5 交叉表 |
| `derived_fields.yaml` | 派生/聚合字段（不参与匹配） |
| `schema.py` | `FieldView` 与源字段统计 |
| `agentic.py` / `agent.py` | 可选智能体对齐（默认 off） |
| `sql.py` / `transform.py` | 固定事件规范的 SQL 对账，不依赖字段映射表 |
| `meds_targets.yaml` | MEDS 接口目标 |

## 已删除

- `_field_map()`（33 条静态映射）；
- `targets_from_gold()`；
- gold precision/recall 评测；
- `field_level_reference.csv` / `LEGACY.md`。
