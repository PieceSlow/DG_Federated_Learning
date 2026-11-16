# alignment 包状态

## 可信目标

- `meds_targets.yaml`：MEDS 接口目标（9 类事件 × 核心 5 列），**唯一目标依据**；
- `targets.py`：加载/校验；不含 `n_*` / `total_los_days` 等自造聚合。

## 旧 33 字段实现（legacy，待替换）

- `schema.py` / `matchers.py` / `combine.py` / `select.py`：旧候选生成与打分；
- `agentic.py` / `agent.py`：旧 AlignerAgent + Verifier；
- `evaluate.py` / `sql.py` / `transform.py`：旧评测与 SQL。

这些代码仍在，是为了保留可运行的基线；**不要用它们评测 MEDS 目标**。
P1-1d 会新建 `matchers/` 包并逐步替换。
