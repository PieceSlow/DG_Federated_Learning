# archive：实验三的历史版本

这里保存实验三早期两个版本的原始脚本，**仅用于演进对照，不参与当前流水线**：

| 目录 | 数据 | 节点 | 说明 |
|---|---|---|---|
| `lab3_v1_synthetic/run_workflow.py` | 脚本内置的合成 EMR | 5 | 首次跑通"对象→MEDS"链路；未装 langgraph 时会退化为顺序执行，适合没有 MIMIC 数据的同学看流程 |
| `lab3_v1_mimic_sample/run_mimic_sample_workflow.py` | 真实 MIMIC 20 人小样本 | 5 | 首次接真实数据；`subject_id` 重编号、`labevents` 只读前 30 万行 |

## 与当前版本的关系

当前流水线在 `governance/`（11 个阶段、100 人队列、文本分支、记录级追溯、MEDS 落盘复验）。
两个历史版本只用于说明方法演进，这里只放代码，不再跟进新产物结构。

## 运行说明

两个脚本都改成了**独立的相对路径**，可以直接运行，但输出写到各自目录下：

```powershell
# 合成数据版：不需要 MIMIC 数据，只需要 pandas/pyarrow
python archive/lab3_v1_synthetic/run_workflow.py

# 真实数据版：需要先将 MIMIC-IV 3.1 放好并设置 MIMIC_ROOT
$env:MIMIC_ROOT = "F:\mimic-iv数据\mimic-iv-3.1"
python archive/lab3_v1_mimic_sample/run_mimic_sample_workflow.py
```

- 两个脚本都使用 `third_party/meds` 的 schema 做校验；
- 输出落在 `archive/<版本>/output*/`，`.gitignore` 已排除；
- v1 的元数据字段（`site_id_columns`、重编号的 `subject_id`）与当前版本不同，
  **不要把它们的产物与 `output/eventstore/` 混用**。
