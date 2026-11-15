"""运行配置：路径解析、目录布局与前置校验。

优先级：命令行参数 > 环境变量 > 仓库内默认值。

环境变量
--------
``MIMIC_ROOT``
    MIMIC-IV 3.1 结构化数据根目录（其下应有 ``hosp/`` 与 ``icu/``）。
``MIMIC_NOTE_ROOT``
    MIMIC-IV-Note 根目录（其下应有 ``note/discharge.csv.gz``）。
``GOVERNANCE_OUTPUT``
    治理产物根目录，默认 ``<repo>/output``。
``MEDS_SRC``
    MEDS 官方 schema 源码目录，默认 ``<repo>/third_party/meds/src``。

对 MIMIC 数据目录**只读**：本系统不复制、不解压、不写入原始数据。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: 仓库根目录（code_repo/）
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 数据文件允许的两种形态
DATA_SUFFIXES = (".csv", ".csv.gz")


def _env_path(env_name: str, default: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else default


def repo_root() -> Path:
    return REPO_ROOT


def default_mimic_root() -> Path:
    return _env_path("MIMIC_ROOT", Path(r"F:\mimic-iv数据\mimic-iv-3.1"))


def default_note_root() -> Path:
    return _env_path("MIMIC_NOTE_ROOT", Path(r"F:\mimic-iv数据"))


def default_output() -> Path:
    return _env_path("GOVERNANCE_OUTPUT", REPO_ROOT / "output")


def meds_src() -> Path:
    """MEDS 官方 schema 所在目录（仅用于按 MEDS 规范校验产物）。"""
    return _env_path("MEDS_SRC", REPO_ROOT / "third_party" / "meds" / "src")


@dataclass(frozen=True)
class Settings:
    """一次治理运行的全部输入参数。"""

    mimic_root: Path
    note_root: Path
    output: Path
    cohort_size: int = 100
    max_notes_per_subject: int = 3
    graph_mode: str = "full"
    graph_delete_missing: bool = False

    @property
    def eventstore(self) -> Path:
        """MEDS 标准事件仓储目录。"""
        return self.output / "eventstore"

    @property
    def governance(self) -> Path:
        """治理资产目录（数据集、映射、对象、标签、分级、目录）。"""
        return self.output / "governance"

    @property
    def audit_dir(self) -> Path:
        return self.output / "audit"

    def ensure_dirs(self) -> None:
        for path in (
            self.eventstore / "data",
            self.eventstore / "metadata",
            self.governance / "dataset",
            self.governance / "mapping",
            self.governance / "metadata",
            self.governance / "quality",
            self.governance / "classification",
            self.governance / "catalog",
            self.audit_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def resolve_data_file(base: Path, group: str, name: str) -> Path:
    """在 ``base/group`` 下按 ``.csv`` → ``.csv.gz`` 顺序查找数据文件。"""
    for suffix in DATA_SUFFIXES:
        candidate = base / group / f"{name}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"未找到数据文件: {base / group / (name + '.csv[.gz]')}\n"
        "请确认 --mimic-root / --note-root（或 MIMIC_ROOT / MIMIC_NOTE_ROOT）指向正确目录。"
    )


def require_mimic_root(path: Path) -> Path:
    if not (path / "hosp").is_dir() and not (path / "icu").is_dir():
        raise FileNotFoundError(
            f"未找到 MIMIC-IV 数据目录: {path}\n"
            "请用 --mimic-root 指定，或设置环境变量 MIMIC_ROOT；"
            "该目录下应包含 hosp/ 与 icu/（.csv 或 .csv.gz 均可）。"
        )
    return path


def require_note_root(path: Path) -> Path:
    candidates = [path / "note" / "discharge.csv", path / "note" / "discharge.csv.gz"]
    if not any(candidate.exists() for candidate in candidates):
        raise FileNotFoundError(
            f"未找到 MIMIC-IV-Note 出院小结: {path / 'note' / 'discharge.csv[.gz]'}\n"
            "请用 --note-root 指定，或设置环境变量 MIMIC_NOTE_ROOT"
            "（该目录下应有 note/discharge.csv 或 discharge.csv.gz）。"
        )
    return path


def require_meds_src(path: Path) -> Path:
    if not (path / "meds" / "schema.py").exists():
        raise FileNotFoundError(
            f"未找到 MEDS schema: {path / 'meds' / 'schema.py'}\n"
            "请确认 third_party/meds 未被删除，或用环境变量 MEDS_SRC 指定。"
        )
    return path
