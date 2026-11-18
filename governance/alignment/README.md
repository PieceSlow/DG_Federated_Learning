# alignment 包状态

## 目标

- `target_schema.yaml`：对象字段目标（只列**由源字段直接提供**的 8 个字段，并限定允许的源表
  与可接受的源字段）；
- `meds_targets.yaml`：MEDS 对外接口的**唯一声明**（10 类事件 × 核心 5 列，每列写
  `source ∈ {spec, derived, mapping, none}`，编码靠 `template` + `uses`）。

## 匹配流程

```text
extract_source_fields（类型/空值率/基数）
  × load_object_targets（目标字段 + 允许源表 + 可接受源字段）
  → matchers（name / type / structure / value）
  → combine（0.45/0.25/0.15/0.15）
  → select（每「目标字段 × 源表」top-3 + 阈值 0.5 + 类型过滤，允许 1:N）
  → field_level.csv（auto/review + method + consumed + evidence）
  → evaluate.coverage（覆盖率，无 gold）
```

## 接口流程

```text
meds_targets.yaml（声明）
  ├→ standardize：按 table / emit_if / template / time / numeric / text 产出事件
  ├→ interface.check_events：命名空间是否都声明过、必填列是否真的非空
  └→ crosswalk：声明 × field_level.csv → field_level_meds.csv（50 行）
        → interface_report：resolution / unmapped / undeclared / required_violations
```

`resolved_by` 五类：`mapping`（落到匹配结果）、`spec`（模板/常量）、`derived`（源行派生）、
`none`（该事件确实没有），以及 `unmapped`（声明要映射却没落地，必须进报告）。

## 文件

| 文件 | 作用 |
|---|---|
| `target_schema.yaml` / `target_schema.py` | 对象字段目标与加载 |
| `meds_targets.yaml` / `targets.py` | MEDS 接口声明与加载（驱动 standardize 与台账） |
| `matchers.py` | 四个匹配器 |
| `combine.py` | 加权组合 |
| `select.py` | 候选 top-k 与类型过滤 |
| `evaluate.py` | 覆盖率（目标/必填/auto/review），不再计算 gold P/R |
| `crosswalk.py` | 接口台账（声明 × 匹配结果） |
| `interface.py` | 接口落地解析、`resolved_by`、事件流自检、接口报告 |
| `derived_fields.yaml` | 派生/聚合/挂接字段（不参与匹配） |
| `schema.py` | `FieldView` 与源字段统计 |
| `agentic.py` / `agent.py` | 可选智能体对齐（默认 off） |
| `sql.py` / `transform.py` | 由映射生成汇聚 SQL（含 `strict_real` 数值口径）并对账 |

## 已删除

- `_field_map()`（33 条静态映射）；
- `targets_from_gold()`；
- gold precision/recall 评测；
- `field_level_reference.csv` / `LEGACY.md`。

## 一致性护栏（测试守着）

- 模板占位符必须在 `uses` 里声明（`tests/test_meds_interface.py`）；
- `join` 必须写 `on_column`（YAML 1.1 会把裸 `on:` 解析成 `true`）；
- 声明引用的源列必须真的在已接入表里（`standardize._check_declared_columns`）；
- 实际事件流的命名空间必须都被声明、必填列必须非空（`interface.check_events`）；
- 接口台账里 `resolved_by=mapping` 必须有落地字段（`crosswalk.validate_crosswalk`）；
- SQL 与事件表逐类型行数 + 逐列非空数一致（`sql.validate_sql`）。
