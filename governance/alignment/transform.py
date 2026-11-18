"""一对多转换：把多个源列合并成一个目标列（SQL 表达式）。"""

from __future__ import annotations


def sql_concat(columns: list[str], alias: str = "t", separator: str = " ") -> str:
    """SQLite 安全的非空拼接：忽略 NULL，保留分隔符。"""
    parts = [f"NULLIF(TRIM(COALESCE({alias}.{column}, '')), '')" for column in columns]
    joined = f" || '{separator}' || ".join(parts)
    return f"TRIM({joined})"


#: 命名的转换函数：名字 → (说明, 目标列, 生成 SQL 的函数)
TRANSFORMS: dict[str, dict] = {
    "dose_text": {
        "description": "剂量 + 单位 + 途径拼接为 text_value",
        "columns": ["dose_val_rx", "dose_unit_rx", "route"],
        "build": lambda alias="t": sql_concat(
            ["dose_val_rx", "dose_unit_rx", "route"], alias=alias
        ),
    }
}


def strict_real(expression: str) -> str:
    """把文本列转成 REAL，口径与 ``pd.to_numeric(errors="coerce")`` 一致。

    SQLite 的 ``CAST('25-50' AS REAL)`` 会得到 25 —— 把"25 到 50 的区间剂量"
    悄悄变成"25"。这样的数值比空值更有害，所以这里显式判形状：

    * 必须是数字（含 ``+/-``、小数点、指数）；
    * ``+``/``-`` 只允许出现在首位（否则 ``25-50``、``3-10`` 会被误判成数字）；
    * 最多一个小数点；
    * 空串/空白/带单位的值一律 NULL。
    """
    value = f"TRIM({expression})"
    return (
        "CASE WHEN "
        f"{value} = '' THEN NULL "
        f"WHEN {value} GLOB '*[0-9]*' "
        f"AND SUBSTR({value}, 2) NOT GLOB '*[+-]*' "
        f"AND {value} NOT GLOB '*[^0-9.eE+-]*' "
        f"AND LENGTH({value}) - LENGTH(REPLACE({value}, '.', '')) <= 1 "
        f"THEN CAST({value} AS REAL) ELSE NULL END"
    )
