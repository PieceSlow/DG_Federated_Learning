"""UMLS 配置：源目录、缓存位置、参与映射的术语体系。"""

from __future__ import annotations

import os
from pathlib import Path

#: 默认 UMLS 安装目录（本机已下载 2026AB，13 GB）
DEFAULT_UMLS_ROOT = Path(r"D:\Documents\umls\2026AB\META")

UMLS_VERSION = "2026AB"

#: 参与概念映射的源词汇表
DEFAULT_SABS: tuple[str, ...] = (
    "ICD10CM",
    "ICD9CM",
    "ICD10PCS",
    "RXNORM",
    "LNC",
    "SNOMEDCT_US",
)

#: 关系白名单（MRREL 过滤，控制缓存体积）
DEFAULT_RELAS: tuple[str, ...] = (
    "may_treat",
    "has_ingredient",
    "has_active_ingredient",
    "has_finding_site",
    "has_manifestation",
    "associated_with",
    "classified_as",
    "same_as",
)


def umls_root() -> Path:
    return Path(os.environ.get("UMLS_ROOT", str(DEFAULT_UMLS_ROOT)))


def cache_root(output_root: Path) -> Path:
    """派生缓存，放在 output/ 下（已被 .gitignore 排除）。"""
    return Path(output_root) / "umls_cache" / UMLS_VERSION


def require_umls_root(path: Path) -> Path:
    if not (path / "MRCONSO.RRF").exists():
        raise FileNotFoundError(
            f"未找到 UMLS 源文件: {path / 'MRCONSO.RRF'}\n"
            "请确认 UMLS_ROOT 指向 .../2026AB/META 目录。"
        )
    return path
