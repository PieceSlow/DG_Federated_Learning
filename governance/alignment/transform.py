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
