"""UMLS 概念链接：源编码/术语 → CUI。

链接策略按可靠性排序：

1. **编码精确匹配**（ICD9 / ICD10 / ICD10PCS）：``SAB + CODE`` 直接命中；
2. **术语精确匹配**（RxNorm 药名、LOINC 检验名）：规范化字符串后命中；
3. **模糊匹配**：仅在精确失败时启用，返回置信度（相似度），供人工复核。

不猜测：匹配不到就返回 ``None``，由质量规则 ``CONCEPT_UNMAPPED`` 记录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path

import pandas as pd

CACHE_FILES = ("terms.parquet", "concepts.parquet")


@dataclass(frozen=True)
class ConceptMatch:
    cui: str
    name: str
    sty: str
    confidence: float
    method: str


def normalize(text: str) -> str:
    value = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())
    value = re.sub(r"\s+", " ", value).strip()
    return value


class ConceptLinker:
    def __init__(self, cache_dir: Path) -> None:
        cache_dir = Path(cache_dir)
        missing = [name for name in CACHE_FILES if not (cache_dir / name).exists()]
        if missing:
            raise FileNotFoundError(
                f"UMLS 缓存不完整: {cache_dir} 缺少 {', '.join(missing)}；"
                "请先运行 python -m governance.umls.preprocess"
            )
        self.terms = pd.read_parquet(cache_dir / "terms.parquet")
        self.concepts = pd.read_parquet(cache_dir / "concepts.parquet").set_index("cui")

        code_terms = self.terms[self.terms["sab"].isin({"ICD10CM", "ICD9CM", "ICD10PCS"})]
        code_terms = code_terms.sort_values("ispref", ascending=False).drop_duplicates(["sab", "code"])
        self._code_index = {
            (sab, code): cui
            for sab, code, cui in zip(code_terms["sab"], code_terms["code"], code_terms["cui"])
        }

        self._lexicon: dict[str, dict[str, tuple[str, str]]] = {}
        self._lexicon_keys: dict[str, list[str]] = {}
        for sab in ("RXNORM", "LNC"):
            subset = self.terms[self.terms["sab"] == sab]  # 保留同义术语，不只取每个 CUI 的首选词
            mapping: dict[str, tuple[str, str]] = {}
            for cui, term in zip(subset["cui"], subset["str"]):
                key = normalize(term)
                if key and key not in mapping:
                    mapping[key] = (cui, str(term))
            self._lexicon[sab] = mapping
            self._lexicon_keys[sab] = list(mapping)
        self._cache: dict[tuple[str, str], ConceptMatch | None] = {}

    # ---- 对外 ------------------------------------------------------------
    def map_icd(self, version: int, code: str) -> ConceptMatch | None:
        sab = "ICD10CM" if int(version) == 10 else "ICD9CM"
        for candidate in _code_candidates(code):
            cui = self._code_index.get((sab, candidate))
            if cui:
                return self._match(cui, 1.0, "icd_exact")
        return None

    def map_procedure(self, version: int, code: str) -> ConceptMatch | None:
        sab = "ICD10PCS" if int(version) == 10 else "ICD9CM"
        for candidate in _code_candidates(code):
            cui = self._code_index.get((sab, candidate))
            if cui:
                return self._match(cui, 1.0, "procedure_exact")
        return None

    def map_drug(self, name: str) -> ConceptMatch | None:
        return self._lexical("RXNORM", name, cutoff=0.9)

    def map_lab(self, label: str) -> ConceptMatch | None:
        return self._lexical("LNC", label, cutoff=0.9)

    # ---- 内部 ------------------------------------------------------------
    def _match(self, cui: str, confidence: float, method: str) -> ConceptMatch | None:
        if cui not in self.concepts.index:
            return None
        row = self.concepts.loc[cui]
        sty = row.get("sty", "")
        return ConceptMatch(
            cui=str(cui),
            name=str(row.get("name", "")),
            sty="" if pd.isna(sty) else str(sty),
            confidence=round(float(confidence), 3),
            method=method,
        )

    def _lexical(self, sab: str, text: str, cutoff: float) -> ConceptMatch | None:
        key = normalize(text)
        if not key:
            return None
        cache_key = (sab, key)
        if cache_key in self._cache:
            return self._cache[cache_key]

        exact = self._lexicon[sab].get(key)
        if exact is not None:
            match = self._match(exact[0], 1.0, "term_exact")
            self._cache[cache_key] = match
            return match

        candidates = self._lexicon_keys[sab]
        best = get_close_matches(key, candidates, n=1, cutoff=cutoff)
        if not best:
            self._cache[cache_key] = None
            return None
        score = SequenceMatcher(None, key, best[0]).ratio()
        match = self._match(self._lexicon[sab][best[0]][0], score, "term_fuzzy")
        self._cache[cache_key] = match
        return match


def _code_candidates(code: str) -> list[str]:
    raw = str(code).strip().upper()
    if not raw:
        return []
    candidates = [raw]
    no_dot = raw.replace(".", "")
    if no_dot not in candidates:
        candidates.append(no_dot)
    if len(no_dot) > 3 and no_dot[:3].isalnum():
        dotted = f"{no_dot[:3]}.{no_dot[3:]}"
        if dotted not in candidates:
            candidates.append(dotted)
    return candidates
