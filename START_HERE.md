# START HERE · 交接说明

> 2026-11-16 ｜ 交接时以 `git log -1` 为准 ｜ 测试：168 个通过
>
> 这个仓库**可以继续开发**，但先记住一句话：**旧 33 字段对齐是中间产物，不是任务结论。**

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
| `governance/alignment/meds_targets.yaml` | ✅ 新目标配置 |
| `clinical_events` / `patient_360` / 33 条映射 | ❌ 旧设计，已隔离 |
| `_archive/untrusted_20261116/` | ❌ 不要引用 |

- 旧产物已移到仓库外 `_archive/untrusted_20261116/`；
- `output/` 当前只有 UMLS 缓存；
- 运行旧 `align` 仍会写 legacy 文件，目录里有 `LEGACY.md`，不要当结论。

## 2. 5 分钟跑起来

```powershell
cd code_repo
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 不依赖 MIMIC 的测试（合成数据）
.venv\Scripts\python -m unittest discover -s tests -t .

# 真实数据小批量（5~10 人）
.venv\Scripts\python run.py --cohort-size 10 --mimic-root "E:\...\mimic-iv-3.1" --note-root "E:\..."
```

数据目录要求：`--mimic-root` 下有 `hosp/`、`icu/`；`--note-root` 下有 `note/discharge.csv.gz`。

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
├── assemble                                       # ⚠️ legacy：clinical_events / patient_360
├── align                                          # ❌ legacy：33 字段，待 P1-1d 替换
├── concept / objectify / label / classify / catalog / report  # ✅ 治理资产
├── alignment/
│   ├── meds_targets.yaml   # ✅ 新目标接口（MEDS 核心 5 列）
│   ├── targets.py          # ✅ 加载/校验
│   ├── matchers/combine/select/agentic  # ⚠️ 旧 33 字段实现，待重写
└── llm/                    # ✅ key/预算/缓存/trace
```

## 4. 下一项任务：P1-1d（从这里继续）

目标：把 `align` 从“33 字段 + 4 个函数”换成“MEDS 目标 + 匹配器 Agent + 组合器 + 约束执行器 + 选择器”。

建议顺序：

1. 新建 `governance/alignment/matchers/`：
   - `base.py`：`MatcherAgent` 协议（role/inputs/outputs/run/trace/matrix）；
   - `name.py`、`type.py`、`structure.py`、`stats.py`；
   - `semantic.py`：LLM 语义匹配器（只发字段元数据）。
2. `matrix.py`：把各 matcher 的 `(target, source, score)` 对齐成同一张长表。
3. `combiner.py`：权重 + 一致性（`alignment/weights.yaml`）；预留训练接口。
4. `constraints.py`：1:1（匈牙利/贪心）、1:N 转换登记、类型/必填校验。
5. `selector.py`：粗筛 top-k → 精排（组合分 + 一致性）→ 定结果。
6. 改 `align.py`：从 `meds_targets.yaml` 读目标，不再调用 `_field_map()`；
   事件类型决定源表上下文（诊断/检验/用药/文书…）。
7. 验收：
   - `--llm off` 可跑，输出 MEDS 字段覆盖率；
   - `--llm mock` 有 matcher/verifier trace；
   - `--llm api --llm-budget-cny 20` 小批量 ≤20 元；
   - 不再引用 33 条 / `patient_360`。

## 5. 不要做

- 不要把 33 条映射、`field_level_reference.csv` 当 gold；
- 不要把 `patient_360` 的 `n_*`、`total_los_days` 放进匹配目标（它们是标签/特征）；
- 不要把行级数据、ID、时间、文本发给 LLM；
- 不要提交 `.env`、`output/`、`_mimic_data/`、UMLS 原始文件；
- 不要把 mock 的结果说成“大模型已建好图谱”。

## 6. 已知坑

1. `standardize.py` 的 MEDS 映射仍是硬编码，尚未由 matcher 生成；
2. `assemble.py` / `catalog.py` 仍依赖 `patient_360`；
3. `docs/附录-实验交付对照.md` 仍是旧口径，P1-1d 后重写；
4. Windows 控制台编码：预算提示已改用 `CNY`，不要再引入 `¥` 到 `print`；
5. 真实 API 只做过一次 tiny smoke，不代表模型质量；
6. `_archive/untrusted_20261116/` 可删，但删前确认没有要对照的内容。

## 7. 交接检查

- [x] 旧产物隔离
- [x] `.env` / 20 元预算
- [x] MEDS 目标接口配置
- [x] 168 测试通过
- [x] API 连通性 smoke
- [ ] 匹配器 Agent + 组合器 + 约束 + 选择器（P1-1d）
- [ ] 小批量 MEDS 端到端
- [ ] legacy 文档全量替换
