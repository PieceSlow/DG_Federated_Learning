"""医疗多源数据治理流水线（代码见 ``governance.pipeline``）。

对外入口：``python run.py`` 或 ``python -m governance``。

以 MIMIC-IV / MIMIC-IV-Note 为输入，把"接入 → 组织标准化 → 质量校验 → 事件仓储 →
汇聚 → 字段对齐 → 对象化 → 标签化 → 分类分级 → 编目 → 报告"串成一条可复现的链路。
"""

__version__ = "3.0.0"
