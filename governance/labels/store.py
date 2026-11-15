"""对象标签存储：``object_labels.parquet``。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class ObjectLabelStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / "object_labels.parquet"

    def write(self, frame: pd.DataFrame) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(self.path, index=False)
        return self.path

    def read(self) -> pd.DataFrame:
        return pd.read_parquet(self.path)
