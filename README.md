# 医疗多源数据治理流水线

以 **MIMIC-IV v3.1 + MIMIC-IV-Note 2.2** 为数据源，把一个本地医疗机构的原始表治理成
**可追溯、可分级、可编目、可共享**的数据资产，并以符合 **MEDS** 规范的事件仓储作为对外接口。

面向的场景是**多中心联邦学习**：各参与方不共享原始病历，但要求数据口径一致、每条记录可回溯、
敏感数据不出本地。因此本系统不追求"把数据拼宽"，而是把治理动作固化成一条可复现的流水线：

> 接入 → 组织标准化 → 质量校验 → 事件仓储 → 汇聚 → 字段对齐 → 对象化 → 标签化 →
> 分类分级 → 编目 → 报告

> **红线**：仓库只提交代码与说明文档。`output/` 下的患者级/文本级派生数据受 PhysioNet
> 协议约束，禁止提交、打包或外发，详见 [docs/07-数据与合规.md](docs/07-数据与合规.md)。

---

## 一、流水线的 12 个阶段

| # | 阶段 | 做什么 | 关键产物 |
|---|---|---|---|
| 01 | 数据接入 | 源表识别、确定性队列、大表分块扫描、数据源清单 | `data_source_inventory.csv` |
| 02 | 组织与标准化 | 对象索引、跨表关联、事件映射、出院小结文本抽取 | 队列与事件（内存） |
| 03 | 质量校验 | 编码/关联/时间/重复校验 + MEDS 核心 schema | `quality/issues.csv` |
| 04 | 事件仓储 | 按 MEDS 目录落盘，写盘后重新读回再校验 | `eventstore/` |
| 05 | 多源汇聚 | 统一事件表 + 患者 360 宽表 | `dataset/*.parquet` |
| 06 | 字段对齐 | 字段级映射（33 条）+ 记录级血缘 | `mapping/*` |
| 07 | 概念归一 | UMLS 编码/药名/检验名 → 概念（CUI）与语义类型 | `concept_map.parquet`、`concept_coverage.json` |
| 08 | 对象化 | 智能体提案对象/关系 → 本地实例化（可回退确定性模型）+ 图谱自检 | `graph/nodes.parquet`、`graph/edges/*`、`object_graph.json` |
| 09 | 标签化 | 对象/术语/章节/敏感级别/划分 5 个维度 | `labels.json` |
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
| `--list-stages` | — | 打印阶段与依赖后退出 |

## 五、产物布局

```text
output/
├── eventstore/                       # MEDS 标准事件仓储
│   ├── data/events.parquet
│   └── metadata/{codes.parquet, subject_splits.parquet, dataset.json}
├── governance/                       # 治理资产
│   ├── dataset/{clinical_events.parquet, patient_360.parquet}
│   ├── mapping/{field_level.csv, record_level.parquet}
│   ├── graph/{nodes.parquet, edges/*.parquet, graph_manifest.json, graph_history.jsonl}
│   ├── metadata/{data_source_inventory.csv, object_graph.json,
│   │             object_inventory.csv, labels.json}
│   ├── quality/{issues.csv, event_stream.json, governed_dataset.json}
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

## 七、参考运行结果（100 人真实队列）

| 指标 | 数值 |
|---|---|
| 队列患者 | 100（78 train / 10 tuning / 12 held_out） |
| MEDS 事件 | 147,640 条、3,558 个编码 |
| 统一事件表 | 24,554 行（处方 18,384 / 诊断 5,319 / 操作 696 / 文书 155） |
| 患者 360 | 100 行 |
| 记录级 / 字段级映射 | 24,654 条 / 33 条 |
| 质量告警 | 158 条（119 时间越界、22 重复、17 缺药品编码），error 0 |
| 运行时间 | 约 3 分钟（读 9s、映射 90s、写盘 48s、治理阶段约 25s） |

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
| [docs/04-汇聚与对齐.md](docs/04-汇聚与对齐.md) | 事件表与患者 360、字段级/记录级映射 |
| [docs/05-对象化与标签化.md](docs/05-对象化与标签化.md) | 对象模型、对象图谱、标签体系 |
| [docs/06-分类分级与编目.md](docs/06-分类分级与编目.md) | 分级规则、五级目录、共享边界 |
| [docs/07-数据与合规.md](docs/07-数据与合规.md) | 许可、分级处置、仓库红线、误提交处理 |
| [docs/08-协作与代码约定.md](docs/08-协作与代码约定.md) | 分支、提交、命名、测试约定 |
| [docs/附录-实验交付对照.md](docs/附录-实验交付对照.md) | 与课程两次实验的交付对应关系 |

## 十一、第三方组件

`third_party/meds/` 是 MEDS 官方 `src/meds/schema.py` 的原样副本（Apache-2.0），
仅用于按官方 schema 校验产物，声明见 `third_party/meds/NOTICE.md`。其余代码均为本组原创。
