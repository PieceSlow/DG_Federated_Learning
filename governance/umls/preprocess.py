"""UMLS 预处理：把 13 GB 的 RRF 过滤成小体积 parquet 缓存。

只保留与治理相关的术语体系与英文术语，以及概念语义类型和少量关系。
首次运行约 10–20 分钟；之后流水线只读缓存。

用法::

    python -m governance.umls.preprocess                 # 默认 D:/Documents/umls/2026AB/META
    python -m governance.umls.preprocess --force         # 强制重建
    python -m governance.umls.preprocess --sabs ICD10CM ICD9CM RXNORM LNC
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from governance.umls.config import (
    DEFAULT_RELAS,
    DEFAULT_SABS,
    UMLS_VERSION,
    cache_root,
    require_umls_root,
    umls_root,
)

CHUNK = 500_000

TERM_SCHEMA = pa.schema(
    [
        ("cui", pa.string()),
        ("sab", pa.string()),
        ("tty", pa.string()),
        ("code", pa.string()),
        ("str", pa.string()),
        ("ispref", pa.string()),
    ]
)
STY_SCHEMA = pa.schema([("cui", pa.string()), ("tui", pa.string()), ("sty", pa.string())])
REL_SCHEMA = pa.schema(
    [
        ("cui1", pa.string()),
        ("rel", pa.string()),
        ("cui2", pa.string()),
        ("rela", pa.string()),
        ("sab", pa.string()),
    ]
)


def _stream(
    path: Path,
    usecols: list[int],
    names: list[str],
    predicate: Callable[[pd.DataFrame], pd.Series],
    out_path: Path,
    schema: pa.Schema,
) -> int:
    """分块读取 RRF，过滤后流式写 parquet，避免整文件进内存。"""
    writer = pq.ParquetWriter(out_path, schema, compression="zstd")
    kept = 0
    try:
        reader = pd.read_csv(
            path,
            sep="|",
            header=None,
            usecols=usecols,
            names=names,
            dtype=str,
            quoting=csv.QUOTE_NONE,
            na_filter=False,
            chunksize=CHUNK,
            on_bad_lines="skip",
        )
        for chunk in reader:
            mask = predicate(chunk)
            filtered = chunk[mask]
            if filtered.empty:
                continue
            table = pa.Table.from_pandas(filtered, schema=schema, preserve_index=False)
            writer.write_table(table)
            kept += len(filtered)
    finally:
        writer.close()
    return kept


def preprocess(
    source_root: Path,
    cache_dir: Path,
    *,
    sabs: Iterable[str] = DEFAULT_SABS,
    relas: Iterable[str] = DEFAULT_RELAS,
    force: bool = False,
) -> dict:
    started = time.perf_counter()
    source_root = require_umls_root(Path(source_root))
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "manifest.json"
    required = ["terms.parquet", "concepts.parquet", "sty.parquet", "rel.parquet"]
    if not force and all((cache_dir / name).exists() for name in required) and manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    sab_set = set(sabs)
    rela_set = set(relas)

    # 1) MRCONSO：英文、未抑制、目标词表
    terms_path = cache_dir / "terms.parquet"
    terms_rows = _stream(
        source_root / "MRCONSO.RRF",
        usecols=[0, 1, 2, 6, 11, 12, 13, 14, 16],
        names=["cui", "lat", "ts", "ispref", "sab", "tty", "code", "str", "suppress"],
        predicate=lambda frame: (frame["lat"] == "ENG")
        & (frame["suppress"] != "Y")
        & frame["sab"].isin(sab_set),
        out_path=terms_path,
        schema=TERM_SCHEMA,
    )

    # 2) 概念表：每个 CUI 取一个首选英文术语
    terms = pd.read_parquet(terms_path)
    terms["_pref"] = (terms["ispref"] == "Y").astype(int)
    terms = terms.sort_values(["cui", "_pref"], ascending=[True, False])
    concepts = terms.drop_duplicates("cui")[["cui", "str"]].rename(columns={"str": "name"})
    concept_set = set(concepts["cui"])
    del terms

    # 3) MRSTY：只保留概念集合内的语义类型
    sty_path = cache_dir / "sty.parquet"
    _stream(
        source_root / "MRSTY.RRF",
        usecols=[0, 1, 3],
        names=["cui", "tui", "sty"],
        predicate=lambda frame: frame["cui"].isin(concept_set),
        out_path=sty_path,
        schema=STY_SCHEMA,
    )
    sty = pd.read_parquet(sty_path)
    sty_join = (
        sty.groupby("cui")["sty"]
        .apply(lambda values: "|".join(sorted(set(values))))
        .rename("sty")
    )
    concepts = concepts.merge(sty_join, left_on="cui", right_index=True, how="left")
    concepts["sty"] = concepts["sty"].fillna("")
    concepts.to_parquet(cache_dir / "concepts.parquet", index=False)

    # 4) MRREL：两端都在概念集合内，且关系类型在关注范围
    rel_path = cache_dir / "rel.parquet"

    def rel_predicate(frame: pd.DataFrame) -> pd.Series:
        mask = frame["cui1"].isin(concept_set) & frame["cui2"].isin(concept_set)
        allowed = frame["rela"].isin(rela_set) | (
            (frame["rela"] == "") & frame["rel"].isin({"RO", "PAR", "CHD"})
        )
        return mask & allowed

    rel_rows = _stream(
        source_root / "MRREL.RRF",
        usecols=[0, 3, 4, 7, 10],
        names=["cui1", "rel", "cui2", "rela", "sab"],
        predicate=rel_predicate,
        out_path=rel_path,
        schema=REL_SCHEMA,
    )

    manifest = {
        "version": UMLS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root),
        "sabs": sorted(sab_set),
        "relas": sorted(rela_set),
        "concepts": int(len(concepts)),
        "terms": int(terms_rows),
        "semantic_types": int(len(sty)),
        "relations": int(rel_rows),
        "elapsed_s": round(time.perf_counter() - started, 1),
        "files": {name: (cache_dir / name).stat().st_size for name in required},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m governance.umls.preprocess",
        description="把 UMLS RRF 过滤成治理用的 parquet 缓存",
    )
    parser.add_argument("--umls-root", type=Path, default=umls_root(), help="UMLS META 目录")
    parser.add_argument("--cache", type=Path, default=None, help="缓存目录（默认 output/umls_cache/<版本>）")
    parser.add_argument("--sabs", nargs="*", default=list(DEFAULT_SABS), help="参与映射的源词表")
    parser.add_argument("--force", action="store_true", help="忽略已有缓存，强制重建")
    return parser


def main(argv: list[str] | None = None) -> int:
    from governance.config import default_output

    args = build_parser().parse_args(argv)
    cache = args.cache or cache_root(default_output())
    manifest = preprocess(args.umls_root, cache, sabs=args.sabs, force=args.force)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
