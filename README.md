# 医疗多源数据治理流水线

以 **MIMIC-IV v3.1 + MIMIC-IV-Note 2.2** 为数据源，把一个本地医疗机构的原始表治理成
**可追溯、可分级、可编目、可共享**的数据资产，并以符合 **MEDS** 规范的事件仓储作为对外接口。

面向的场景是**多中心联邦学习**：各参与方不共享原始病历，但要求数据口径一致、每条记录可回溯、
敏感数据不出本地。因此本系统不追求"把数据拼宽"，而是把治理动作固化成一条可复现的流水线：

> 接入 → 组织标准化 → 质量校验 → 事件仓储 → 汇聚 → 字段对齐 → 对象化 → 标签化 →
> 分类分级 → 编目 → 报告

> **红线**：仓库只提交代码与说明文档。`output/` 下的患者级/文本级派生数据受 PhysioNet
> 协议约束，禁止提交、打包或外发，详见 [docs/07-数据与合规.md](docs/07-数据与合规.md)。

> **新接手先读 [START_HERE.md](START_HERE.md)**；策略变更看
> [docs/11-变更记录与决策日志.md](docs/11-变更记录与决策日志.md)。

> **2026-11-18 字段对齐重构**：删除 33 条静态映射。目标字段来自
> `governance/alignment/target_schema.yaml`；候选由 name/type/structure/value 四个
> 匹配器生成，**每个「目标字段 × 源表」**保留 top-k，`source_fields` 是声明契约
> （声明过的字段不被阈值否掉），输出 `field_level.csv`（auto/review + method）。
> 派生/聚合字段单独放 `derived_fields.csv`；`field_level.csv` 的 `consumed` 列标出
> 哪些映射真的被下游用上（38 条里 19 条）。
>
> **SQL 由映射生成**：`build_clinical_events.sql` 的每个字段表达式来自 `field_level.csv`，
> `sql_execution_report.json.mapping_usage` 给出 mapping/transform/spec/derived/link/
> postprocess/missing 的计数，并与事件表**逐类型行数 + 逐列非空数**对账。
>
> **MEDS 接口由声明驱动**：`governance/alignment/meds_targets.yaml` 是接口唯一声明
> （11 类事件 × 5 个核心字段，每列写 `source ∈ spec/derived/mapping/none`），
> `standardize` 按它产出事件，`crosswalk` 用声明 × 匹配结果生成
> `field_level_meds.csv`（55 行，带 `resolved_by`），`interface.check_events` 校验
> 实际事件流的命名空间与必填列；结果进 `quality/meds_interface_report.json`。
>
> **标签建在图谱之上**：`label` 阶段依赖 `graph`，住院级计数读边表、共病走两跳、
> 概念对象主键与图谱 Concept 节点统一（`concept_key.concept_object_id`），
> 口径不一致产出 `LABEL_GRAPH_MISMATCH`。

---

## 一、流水线的 12 个阶段

| # | 阶段 | 做什么 | 关键产物 |
|---|---|---|---|
| 01 | 数据接入 | 源表识别、确定性队列、大表分块扫描、数据源清单 | `data_source_inventory.csv` |
| 02 | 组织与标准化 | 对象索引、跨表关联、事件映射、出院小结文本抽取 | 队列与事件（内存） |
| 03 | 质量校验 | 编码/关联/时间/重复校验 + MEDS 核心 schema | `quality/issues.csv` |
| 04 | 事件仓储 | 按 MEDS 目录落盘，写盘后重新读回再校验 | `eventstore/` |
| 05 | 多源汇聚 | 统一事件表 + 患者 360 资源视图 | `dataset/*.parquet` |
| 06 | 字段对齐 | schema matching：目标 schema + 四个匹配器 + 每个「目标字段 × 源表」top-k；**用映射生成组织汇聚 SQL 并对账** | `mapping/field_level.csv`、`build_clinical_events.sql`、`derived_fields.csv`、`field_level_meds.csv` |
| 07 | 概念归一 | UMLS 编码/药名/检验名 → 概念（CUI）与语义类型；无 UMLS 缓存时跳过、不生成产物 | `concept_map.parquet`、`concept_coverage.json` |
| 08 | 对象化 | 智能体提案对象/关系 → 本地实例化（可回退确定性模型）+ 图谱自检 | `graph/nodes.parquet`、`graph/edges/*`、`object_graph.json` |
| 09 | 标签化 | KV 对象标签 L1–L4，**读图谱算**（边表计数、两跳共病、概念节点） | `labels/object_labels.parquet`、`labels/tag_dictionary.csv`、`labels.json` |
| 10 | 分类分级 | 7 条分级规则 + 落地结果 | `classification/*.csv` |
| 11 | 编目 | 资源/资产/服务/共享/开放五级目录 | `data_catalog.json/.md` |
| 12 | 报告 | 事件流与治理后数据集的质量汇总 | `quality/*.json`、`run_summary.json` |

```text
MIMIC-IV / Note（只读）
      │  01 接入：定队列、取全量历史
      ▼
  对象与事件（内存）──02 标准化──► 03 校验 ──► 04 事件仓储（MEDS）
                                              │
                                              ▼
                              05 汇聚（事件表 + 患者 360）
                                              │
                              06 对齐（字段级/记录级血缘）
                                              │
                              07 概念归一（UMLS，无缓存则跳过）
                                              │
                              08 对象化（对象图谱）
                                              │
                              09 标签化（语义 + 敏感级别）
                                              │
                              10 分级 ──► 11 编目 ──► 12 报告
```

阶段之间通过同一个 `GovernanceContext` 传递状态；每个阶段声明自己依赖哪些前置结果，
缺了会直接报"缺什么"，而不是在深层调用里连累报错。任一阶段也可以单独重跑（`--stages`）。

## 二、目录结构

```text
code_repo/
├── run.py                      # 唯一入口
├── governance/
│   ├── pipeline.py             # 阶段编排（LangGraph StateGraph）
│   ├── umls/                   # UMLS 预处理缓存与概念链接
│   ├── agents/                 # 智能体基座（M1）：协议、视图、Supervisor
│   ├── llm/                    # LLM 接入：provider/缓存/预算/审计
│   ├── prompts/                # 各 Agent 的 prompt（YAML，带版本）
│   ├── config.py               # 路径与运行参数
│   ├── context.py              # 运行上下文：状态、审计、问题清单
│   ├── meds.py                 # MEDS 规范适配：schema、编码格式、划分
│   ├── ingest.py               # 01 数据接入
│   ├── standardize.py          # 02 组织与标准化
│   ├── quality.py              # 03 质量校验
│   ├── materialize.py          # 04 事件仓储落盘
│   ├── assemble.py             # 05 多源汇聚
│   ├── align.py                # 06 字段对齐
│   ├── objectify.py            # 07 对象化
│   ├── label.py                # 08 标签化
│   ├── classify.py             # 09 分类分级
│   ├── catalog.py              # 10 编目
│   ├── report.py               # 11 报告
│   ├── text_extraction.py      # 出院小结的确定性规则抽取
│   └── cli.py                  # 命令行解析
├── tests/                      # 单元测试 + 合成数据端到端测试
├── docs/                       # 按治理环节组织的设计文档
├── third_party/meds/           # MEDS 官方 schema（Apache-2.0）
├── archive/                    # 早期版本（仅对照，不再维护）
├── tools/slides/               # 汇报 PPT 生成脚本（可选）
└── output/                     # 运行产物，已被 .gitignore 排除
```

## 三、快速开始

> API key 放 `code_repo/.env`（复制 `.env.example`），不要写入仓库；默认预算 **20 元**，
> 50%/80% 告警、100% 停止。详见 [docs/00-本地运行与密钥.md](docs/00-本地运行与密钥.md)。


```powershell
# 1. 安装依赖（国内网络建议加镜像：-i https://pypi.tuna.tsinghua.edu.cn/simple）
python -m pip install -r requirements.txt

# 2. 环境自检（依赖、数据路径、MEDS schema）
python scripts/check_env.py

# 3. 配置数据路径：参数或环境变量二选一
$env:MIMIC_ROOT      = "F:\mimic-iv数据\mimic-iv-3.1"   # 含 hosp/ 与 icu/
$env:MIMIC_NOTE_ROOT = "F:\mimic-iv数据"                 # 含 note/discharge.csv.gz

# 4. 跑完整流水线
python run.py

# 常用变体
python run.py --cohort-size 20                  # 小队列试跑
python run.py --stages assemble,align,label     # 只重跑某几段
python run.py --list-stages                     # 看阶段与依赖
python -m governance                            # 与 run.py 等价
```

Windows 一键脚本：`powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1 -CohortSize 100`

**没有真实数据也能验证**：`tests/synthetic.py` 会生成一份同构的小样本（3 名患者），
`python -m unittest tests.test_integration_synthetic` 即可跑通全部 11 个阶段——
这也是团队协作时确认"代码没坏"的最快方式。

## 四、命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--mimic-root` | `$MIMIC_ROOT` 或 `F:\mimic-iv数据\mimic-iv-3.1` | MIMIC-IV 结构化数据根目录 |
| `--note-root` | `$MIMIC_NOTE_ROOT` 或 `F:\mimic-iv数据` | MIMIC-IV-Note 根目录 |
| `--output` | `$GOVERNANCE_OUTPUT` 或 `output/` | 治理产物根目录 |
| `--cohort-size` | 100 | 队列患者数（`icustays` 中 `subject_id` 最小的 N 名） |
| `--max-notes-per-subject` | 3 | 每名患者最多使用几份出院小结 |
| `--stages` | 全部 | 只运行指定阶段，逗号分隔 |
| `--llm` | `off` | 智能体推理内核：`off` 确定性建图 / `mock` 演示 / `api` 真实调用 |
| `--llm-model` | `deepseek-flash` | API 模式使用的模型 |
| `--llm-budget-usd` | 1.0 | API 费用上限（护栏） |
| `--llm-max-calls` | 200 | API 调用次数上限 |
| `--graph-mode` | `full` | 图谱写入模式：`full` 整图重建 / `incremental` 增量 upsert |
| `--graph-delete-missing` | false | 增量模式下删除本轮未见到的节点/边 |
| `--manual-labels` | 无 | 手动标签 CSV（`object_type,object_id,key,value`） |
| `--list-stages` | — | 打印阶段与依赖后退出 |

## 五、产物布局

```text
output/
├── eventstore/                       # MEDS 标准事件仓储
│   ├── data/events.parquet
│   └── metadata/{codes.parquet, subject_splits.parquet, dataset.json}
├── governance/                       # 治理资产
│   ├── dataset/{clinical_events.parquet, patient_360.parquet}   # 资源数据集
│   ├── mapping/{field_level.csv, derived_fields.csv, field_level_meds.csv,
│   │             field_level_auto.csv, field_level_agent.csv, record_level.parquet,
│   │             match_candidates.parquet, build_clinical_events.sql}
│   ├── graph/{nodes.parquet, edges/*.parquet, graph_manifest.json, graph_history.jsonl}
│   ├── metadata/{data_source_inventory.csv, object_graph.json,
│   │             object_inventory.csv, labels.json}
│   ├── labels/{object_labels.parquet, tag_dictionary.csv}   # KV 标签（L1–L4）
│   ├── quality/{issues.csv, event_stream.json, governed_dataset.json,
│   │             concept_coverage.json, alignment_report.json,
│   │             sql_execution_report.json, alignment_agent_report.json,
│   │             label_coverage.json}
│   ├── classification/{rules.csv, result.csv}
│   ├── metadata/concept_map.parquet          # 编码 → UMLS CUI
│   └── quality/concept_coverage.json         # 概念覆盖率
│   └── catalog/{data_catalog.json, data_catalog.md}
├── audit/audit_log.csv               # 每阶段动作、计数、时间
└── run_summary.json                  # 阶段耗时与规模摘要
```

| 产物 | 可否提交 |
|---|---|
| `output/**`（事件、宽表、映射、问题明细、审计） | ❌ 含 `subject_id`/`hadm_id`/文本原文，禁止 |
| `output/governance/quality/*.json`、`object_graph.json`、`catalog/*` | ⚠️ 聚合产物，可少量摘录到文档，但需逐项确认无患者行 |

## 六、设计取舍

| 取舍 | 原因 |
|---|---|
| 用 MEDS 作为事件流标准 | 联邦要求各中心同一套事件语义；MEDS 有官方 schema，可机器校验 |
| 用 LangGraph 编排阶段 | 阶段边界清晰、可单独重跑、每步都有审计；而不是一个巨型 `main()` |
| 文本抽取用规则而非大模型 | 本机 14B 模型约 4–5 token/s，155 份小结需数小时且输出不稳定；规则可复现可审计 |
| 时间越界只标记不改写 | 出院带药、入院前用药是真实临床情况，静默修正会破坏可审计性 |
| 聚合行用 subject 级血缘 | `patient_360` 无法一对一落到单条源记录，不伪造映射 |
| 事件仓储与治理资产分开 | 前者是对外标准接口，后者是内部资产，混在一起会模糊共享边界 |
| 边当表存 | 每种关系一张 parquet：可直接 join/查询、可加属性、可增量，不必部署图数据库 |
| 概念层用 UMLS | 跨中心对齐需要统一概念；UMLS 只在本地缓存，不联网、不外发 |

## 七、参考运行结果（100 人真实队列，2026-11-18 复跑）

| 指标 | 数值 |
|---|---|
| 队列患者 | 100（78 train / 10 tuning / 12 held_out） |
| MEDS 事件 | 148,336 条、3,892 个编码 |
| 统一事件表 | 24,554 行（处方 18,384 / 诊断 5,319 / 操作 696 / 文书 155） |
| 患者 360 | 100 行 |
| 记录级映射 | 24,654 条（24,554 事件 + 100 患者） |
| 字段级映射 | 38 条（31 matcher / 7 declared；22 auto / 16 review），覆盖 8 个目标字段 |
| 组织汇聚 SQL | 内存 SQLite 执行 24,554 行，与事件表逐类型一致（`status=ok`） |
| SQL 字段来源 | mapping 19 / transform 1 / spec 2 / derived 32 / missing 10 |
| 对象图谱 | 8 类对象、9 类关系；Concept 2,262 个（UMLS CUI 2,042 + 编码级兜底 220） |
| KV 标签 | 136,732 条、19 个 tag_key（含 `L2.assertion`/`L2.certainty`）；概念对象 2,262/2,263 命中图谱节点（差 1 条文书概念） |
| 质量告警 | 925 条（含 `CONCEPT_UNMAPPED` 481），error 0；详见 [docs/03](docs/03-质量校验与事件仓储.md) |
| 运行时间 | 130 秒（读取 10s、标准化 17s、写盘 38s、对齐 46s、治理阶段约 19s） |

> 告警从旧文档的 158 条增至 925 条（无 UMLS 时为 444），原因是**文本一致性、图谱一致性
> 与概念映射规则都已生效**：
> 158（时间越界 119 + 重复 22 + 缺编码 17）+ 文本用药不一致 246（明细按规则上限 200 记）
> + 文书缺章节 19 + 文本与 ICD 不一致 17 + 图谱时间冲突 1。

## 八、测试

```powershell
python -m unittest discover -s tests -t .        # 全部
python -m unittest tests.test_integration_synthetic -v   # 端到端
```

- 单元测试覆盖：配置与路径、ICD 章节与敏感级别、MEDS schema、质量规则、文本抽取、阶段编排；
- 集成测试用合成数据跑完 11 个阶段，并断言产物齐全、schema 通过、三类质量问题被触发。

## 九、Agent 层（M1 + M2.6a，默认关闭）

智能体层与现有流水线**解耦**：`python run.py` 的行为、阶段数、产物路径完全不变；
Agent 只有显式调用时才工作。

```powershell
python -m governance.agents.demo              # mock 演示：零网络、零成本
python -m governance.agents.demo --llm off    # 演示关闭时明确报错、优雅降级
python -m governance.agents.demo --mimic-root "E:\mimic-iv数据\mimic-iv-3.1"  # 用真实表结构
```

**P1-1c 对齐智能体**：`--llm mock/api` 时，`AlignerAgent` 从本地 top-k 候选中选映射，
`AlignmentVerifierAgent` 用类型/空值率/候选合法性驳回，最多重试 2 次；
`--llm off` 回退到匹配器选择。

**M2.6a 智能体建图**：`ObjectFinderAgent`（提对象）→ 校验器（表/主键必须真实）→
`RelationExtractorAgent`（提关系）→ 本地 `build_graph` 执行 → `GraphCriticAgent`（审缺陷）→
必要时回退关系抽取重试。`--llm off` 时回退到 `objects.yaml` 的确定性模型，结果可复现；
所有调用写 `output/audit/agent_trace.jsonl`。

**图谱写入**：默认 `full`（整图重建，同一输入可复现）；`--graph-mode incremental` 按主键
upsert 节点/边并保留历史，`graph_history.jsonl` 记录每次运行的差量。

M1 包含：

| 模块 | 作用 |
|---|---|
| `agents/base.py` | Agent 协议：role/goal/inputs/outputs/tools + `AgentResult` + trace |
| `agents/views.py` | 视图层：把本地数据压成 schema/统计/候选视图，并做患者数据闸门 |
| `agents/supervisor.py` | 分派 + 校验 + 带反馈重试（骨架） |
| `agents/profiler.py` | SourceProfilerAgent：第一个可跑的 Agent |
| `llm/` | provider（off/mock/api）、响应缓存、预算护栏、`llm_usage.jsonl` |
| `prompts/profiler.yaml` | prompt 与输出 JSON schema，版本化 |

安全约束：

- 所有出域内容必须经 `views.py`，`require_no_patient_data` 会拦截患者标识/时间戳/文本；
- 默认 `--llm off`；`mock` 不联网；`api` 需要 `DEEPSEEK_API_KEY`，key 不入库；
- 审计：`output/audit/agent_trace.jsonl`、`output/audit/llm_usage.jsonl`（`output/` 已被忽略）。

文本抽取仍走**本地规则 + 经典 NLP**，与 Agent 层无关。

## 十、文档

| 文档 | 内容 |
|---|---|
| [docs/01-总体设计.md](docs/01-总体设计.md) | 场景、架构、阶段与数据流、口径一致性 |
| [docs/02-数据接入与标准化.md](docs/02-数据接入与标准化.md) | 队列抽样、分块扫描、编码命名空间、文本分支 |
| [docs/03-质量校验与事件仓储.md](docs/03-质量校验与事件仓储.md) | 校验规则、MEDS 落盘、读回复验 |
| [START_HERE.md](START_HERE.md) | 交接入口：可信边界、5 分钟运行、P1-1d 任务 |
| [docs/09-状态与下一阶段.md](docs/09-状态与下一阶段.md) | 完整交付状态、路线图、已知问题 |
| [docs/10-字段对齐与标签最终口径.md](docs/10-字段对齐与标签最终口径.md) | schema matching、派生字段、KV 标签与验收 |
| [docs/00-本地运行与密钥.md](docs/00-本地运行与密钥.md) | `.env`、API key、20 元预算、小批量实验 |
| [docs/04-汇聚与对齐.md](docs/04-汇聚与对齐.md) | 事件表与患者 360、字段级/记录级映射（待按 MEDS 目标重构） |
| [docs/05-对象化与标签化.md](docs/05-对象化与标签化.md) | 对象模型、对象图谱、标签体系 |
| [docs/06-分类分级与编目.md](docs/06-分类分级与编目.md) | 分级规则、五级目录、共享边界 |
| [docs/07-数据与合规.md](docs/07-数据与合规.md) | 许可、分级处置、仓库红线、误提交处理 |
| [docs/08-协作与代码约定.md](docs/08-协作与代码约定.md) | 分支、提交、命名、测试约定 |
| [docs/附录-实验交付对照.md](docs/附录-实验交付对照.md) | 与课程两次实验的交付对应关系 |
| [docs/11-变更记录与决策日志.md](docs/11-变更记录与决策日志.md) | 每批策略变更的原因、影响与验证 |

## 十一、图谱可视化（可选，给汇报用）

`scripts/draw_graph.py` 读 `governance/graph/` 的节点/边，出两张图（需要 `networkx` + `matplotlib`）：

```powershell
python scripts/draw_graph.py --graph-dir output/governance/graph --out-dir output/graphs
```

- `graph_classes.png`：**UML 类图**（类 = 对象类型，字段 = 主键/属性，关联线标 1:N / N:1），
  适合放进「对象模型」页；
- `graph_schema.png` / `.dot`：类型级 schema（对象类型 + 关系 + 实例数），**不含患者信息**，
  可以直接放进汇报；
- `graph_Patient_<id>.png`：单个患者的实例图（Patient → Encounter → 事件 → 概念 CUI），
  含 `subject_id`，**只用于本地演示，不要提交或外传**；
- 默认自动挑"1 次住院、事件数适中"的患者，也可以
  `--patient 10004113 --max-events 18` 指定。

多智能体链路的证据在 `output/audit/agent_trace.jsonl`（每次调用一行：agent、step、decision、
tokens、cost）；`--llm mock` 会留下"驳回 → 重试"的记录，`--llm api` 才是真实调用。

## 十二、第三方组件

`third_party/meds/` 是 MEDS 官方 `src/meds/schema.py` 的原样副本（Apache-2.0），
仅用于按官方 schema 校验产物，声明见 `third_party/meds/NOTICE.md`。其余代码均为本组原创。
