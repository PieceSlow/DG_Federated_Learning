# START HERE · 交接说明

> 2026-11-18 ｜ 交接时以 `git log -1` 为准 ｜ 测试：222 个通过
>
> 字段对齐已改为 **schema matching**：目标 schema + name/type/structure/value 四个匹配器
> + 每个「目标字段 × 源表」top-k + 声明契约；**33 条静态映射表已删除**。
>
> 三条链路已打通：**字段映射 → 组织汇聚 SQL**（不再硬编码）、
> **接口声明 → 事件 → 接口台账**（不再代码一套、对照表一套）、
> **对象图谱 → 标签**（不再各算一套）。

## 0. 先读什么

1. 本文件；
2. [docs/09-状态与下一阶段.md](docs/09-状态与下一阶段.md)（完整状态、可信边界、路线图）；
3. [README.md](README.md)（流水线 12 阶段）；
4. [docs/00-本地运行与密钥.md](docs/00-本地运行与密钥.md)（key、预算、小批量）。

## 1. 可信 / 不可信

| 来源 | 用途 |
|---|---|
| MIMIC-IV 原始表 + 数据字典 | ✅ 源数据 |
| **MEDS 接口（助教确定）** | ✅ 目标接口 |
| Lab4 任务文本 | ✅ 要求 |
| `governance/alignment/target_schema.yaml` | ✅ 对象字段目标（8 个源字段） |
| `governance/alignment/matchers.py` 等 | ✅ 候选生成、组合、选择 |
| `mapping/field_level.csv` | ✅ 匹配结果（auto/review，无静态表） |
| `mapping/derived_fields.csv` | ✅ 派生/聚合字段说明 |
| `governance/alignment/meds_targets.yaml` | ✅ MEDS 接口唯一声明（驱动 `standardize`） |
| `mapping/field_level_meds.csv` | ✅ MEDS 接口台账（50 行，带 `resolved_by`） |
| `_archive/untrusted_20261116/` | ❌ 旧归档，不引用 |

- `output/` 行级产物不入库；
- `align` 运行时生成 `field_level.csv`、`derived_fields.csv`、`field_level_meds.csv`；
  不再有 `LEGACY.md` 或 33 条静态表。

## 2. 5 分钟跑起来

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 不依赖 MIMIC 的测试（合成数据）
.venv\Scripts\python -m unittest discover -s tests -t .

# 真实数据小批量（5~10 人）
.venv\Scripts\python run.py --cohort-size 10 --mimic-root "E:\...\mimic-iv-3.1" --note-root "E:\..."
```

数据目录要求：`--mimic-root` 下有 `hosp/`、`icu/`；`--note-root` 下有 `note/discharge.csv.gz`。

> **数据与密钥不随仓库转发**：MIMIC-IV 需要 PhysioNet 认证，UMLS 需要许可证，API key 需要接手人自己的。

### API key（只放本地）

```powershell
Copy-Item .env.example .env
notepad .env      # 填 DEEPSEEK_API_KEY=sk-...
```

- `.env` 已 gitignore；
- 默认预算 20 元，50%/80%/100% 预警、100% 停止；
- 先 `--llm mock` 验证协作，再 `--llm api --llm-budget-cny 20`。

## 3. 代码地图

```text
governance/
├── ingest / standardize / quality / materialize   # ✅ 可信主干，MEDS eventstore
├── assemble                                       # ✅ 资源数据集：clinical_events / patient_360
├── align                                          # ✅ schema matching：目标 schema + 匹配器 + top-k + 约束
├── concept / objectify / label / classify / catalog / report  # ✅ 治理资产
├── alignment/
│   ├── target_schema.yaml  # ✅ 对象字段目标（仅源字段直接提供的 8 个字段）
│   ├── target_schema.py    # ✅ 加载目标与允许的源表
│   ├── matchers.py         # ✅ name/type/structure/value 四个匹配器
│   ├── combine.py          # ✅ 加权组合
│   ├── select.py           # ✅ 候选 top-k + 类型过滤
│   ├── evaluate.py         # ✅ 覆盖率（无 gold）
│   ├── crosswalk.py        # ✅ MEDS 接口台账（声明 × 匹配结果）
│   ├── interface.py        # ✅ 接口落地解析与事件流自检
│   ├── derived_fields.yaml # ✅ 派生/聚合字段（不参与匹配）
│   └── meds_targets.yaml   # ✅ MEDS 接口（对外）
└── llm/                    # ✅ key/预算/缓存/trace
```

## 4. 字段对齐现状

已实现：

1. `target_schema.yaml` 定义 8 个由源字段直接提供的目标字段（subject_id、hadm_id、
   stay_id、event_time、code、display、numeric_value、text_value），并限定允许的源表与
   可接受的源字段；
2. 四个匹配器出分 → 加权组合 → **每个「目标字段 × 源表」**保留 top-3（否则
   `diagnoses_icd.subject_id` 会被挤出候选）；`source_fields` 声明的字段直接准入；
3. `field_level.csv` 输出 `auto` / `review` 与 `method=matcher/declared`，
   `derived_fields.csv` 单独记录 record_id/event_type/patient_360 聚合等不参与匹配的字段；
4. **`build_clinical_events.sql` 由映射生成**，`mapping_usage` 给出每个字段的来源
   （mapping/transform/spec/derived/link/postprocess/missing），并与事件表逐列对账；
5. 覆盖率报告：目标覆盖、必填覆盖、auto/review 数量、缺失必填项；
6. 没有 33 条 gold，也不再计算 precision/recall。

## 4.1 接口现状

`meds_targets.yaml` 是 MEDS 接口的**唯一声明**：10 类事件 × 5 个核心字段，每列写
`source ∈ {spec, derived, mapping, none}`，编码靠 `template` + `uses`；`standardize`
按声明产出事件；`crosswalk` 用声明 × `field_level.csv` 生成 50 行台账，回填
`matched_source_*` 与 `resolved_by`；`interface.check_events` 校验实际命名空间与必填列。
`procedures_icd` 目前不在接口里（`source_gaps` 写了补法），检验文本 `value` 未接入。

## 4.2 标签现状

`label` 依赖 `clinical_events` + `graph`：住院级计数读边表、`L4.comorbidity_burden`
走两跳、Concept 主键与图谱节点统一（`concept_key.concept_object_id`）；
不一致产出 `LABEL_GRAPH_MISMATCH`。无 UMLS 时图谱建编码级降级概念节点。

可选增强（不阻塞交付）：语义匹配器（LLM，只发字段元数据）、1:1 约束的匈牙利算法、
人工复核界面、跨源实体识别。

## 5. 不要做

- 不要再加静态字段映射表当“对齐结果”；
- 不要把 `patient_360` 的 `n_*`、`total_los_days` 放进匹配目标（它们是派生/聚合）；
- 不要把行级数据、ID、时间、文本发给 LLM；
- 不要提交 `.env`、`output/`、`_mimic_data/`、UMLS 原始文件；
- 不要把 mock 的结果说成“大模型已建好图谱”。

## 6. 已知坑

1. `procedures_icd` 没有 MEDS 事件（只有 `clinical_events.Procedure`），补法见
   `meds_targets.yaml` 的 `source_gaps`；
2. 38 条字段映射里 19 条未被 SQL 消费（`consumed=false`），覆盖率不等于"用上了"；
3. `catalog.py` 仍引用 `patient_360`（资源视图）；
4. `docs/04` / `docs/05` 已按新口径更新，若发现旧措辞以 `docs/09` 为准；
5. Windows 控制台编码：预算提示已改用 `CNY`，不要再引入 `¥` 到 `print`；
6. 真实 API 只做过一次 tiny smoke，不代表模型质量；
7. `_archive/untrusted_20261116/` 可删，但删前确认没有要对照的内容；
8. YAML 1.1 会把裸 `on:` 解析成布尔 `true`，接口声明的 join 所以用 `on_column`。

## 7. 交接检查

- [x] schema matching（无 33 条静态表）
- [x] KV 对象标签 + 标签字典 + 覆盖率
- [x] MEDS 接口声明驱动 + 50 行接口台账（`unmapped` 0）
- [x] `.env` / 20 元预算
- [x] 222 测试通过
- [ ] 小批量真实数据端到端（API ≤20 元）
- [ ] 100 人全量运行（需数据盘）
