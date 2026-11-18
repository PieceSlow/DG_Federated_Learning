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
import yaml

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


#: 药名里的剂型/规格噪声：清掉后再匹配，"OxyCODONE (Immediate Release)" → "oxycodone"
_DRUG_NOISE = {
    "solution", "injectable", "injection", "suspension", "oral", "tablet", "tablets",
    "capsule", "capsules", "usp", "human", "recombinant", "intravenous", "flush",
    "syringe", "vial", "bag", "pack", "kit",
    "mg", "ml", "g", "mcg", "meq", "unt", "unit", "units", "eq", "per", "each",
}


def clean_term(text: str) -> str:
    """药名/检验名的"去噪声"规范化：去括号内容、剂型、纯数字。"""
    without_parens = re.sub(r"\([^)]*\)", " ", str(text))
    tokens = [
        token
        for token in normalize(without_parens).split()
        if token not in _DRUG_NOISE and not re.fullmatch(r"[\d.]+", token)
    ]
    return " ".join(tokens)


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

        code_terms = self.terms[self.terms["sab"].isin({"ICD10CM", "ICD9CM", "ICD10PCS"})].copy()
        # 同一编码可能有多个 CUI：优先有语义类型、首选术语（PT）、ISPREF=Y 的候选
        sty_present = self.concepts["sty"].fillna("").astype(str).str.strip().ne("")
        code_terms["_has_sty"] = code_terms["cui"].map(sty_present).fillna(False).astype(int)
        code_terms["_is_pt"] = (code_terms["tty"] == "PT").astype(int)
        code_terms["_is_pref"] = (code_terms["ispref"] == "Y").astype(int)
        code_terms = (
            code_terms.sort_values(
                ["sab", "code", "_has_sty", "_is_pt", "_is_pref"],
                ascending=[True, True, False, False, False],
            ).drop_duplicates(["sab", "code"])
        )
        self._code_index = {
            (sab, code): cui
            for sab, code, cui in zip(code_terms["sab"], code_terms["code"], code_terms["cui"])
        }

        self._lexicon: dict[str, dict[str, tuple[str, str]]] = {}
        self._lexicon_keys: dict[str, list[str]] = {}
        self._token_index: dict[str, dict[str, set[str]]] = {}
        for sab in ("RXNORM", "LNC"):
            subset = self.terms[self.terms["sab"] == sab]  # 保留同义术语，不只取每个 CUI 的首选词
            mapping: dict[str, tuple[str, str]] = {}
            for cui, term in zip(subset["cui"], subset["str"]):
                key = normalize(term)
                if key and key not in mapping:
                    mapping[key] = (cui, str(term))
            self._lexicon[sab] = mapping
            self._lexicon_keys[sab] = list(mapping)
            token_index: dict[str, set[str]] = {}
            for key in mapping:
                for token in set(key.split()):
                    if len(token) >= 3:
                        token_index.setdefault(token, set()).add(key)
            self._token_index[sab] = token_index
        # 药名"去噪声"索引：{} 里的品牌/剂型不影响概念；只用成分/商品名/规范名
        self._cleaned: dict[str, tuple[str, str]] = {}
        drug_terms = self.terms[
            (self.terms["sab"] == "RXNORM")
            & (self.terms["tty"].isin({"IN", "SY", "PSN", "SCD", "SBD"}))
        ]
        for cui, term in zip(drug_terms["cui"], drug_terms["str"]):
            key = clean_term(term)
            if key and key not in self._cleaned:
                self._cleaned[key] = (str(cui), str(term))

        # 成分级（TTY=IN）索引 + token 倒排，用于"复方/带规格药名 → 成分"兜底
        ingredients = self.terms[(self.terms["sab"] == "RXNORM") & (self.terms["tty"] == "IN")]
        self._ingredient: dict[str, tuple[str, str]] = {}
        for cui, term in zip(ingredients["cui"], ingredients["str"]):
            key = clean_term(term)
            if key and key not in self._ingredient:
                self._ingredient[key] = (str(cui), str(term))
        self._ingredient_tokens: dict[str, set[str]] = {}
        for key in self._ingredient:
            for token in set(key.split()):
                if len(token) >= 3:
                    self._ingredient_tokens.setdefault(token, set()).add(key)

        # 显式别名（可审计）：只收语义等价的通用名，见 drug_aliases.yaml
        self._aliases: dict[str, str] = {}
        alias_path = Path(__file__).with_name("drug_aliases.yaml")
        if alias_path.exists():
            raw = yaml.safe_load(alias_path.read_text(encoding="utf-8")) or {}
            for key, value in (raw.get("aliases") or {}).items():
                self._aliases[normalize(key)] = str(value)

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
        """药名 → 概念：精确 → 去噪声精确 → 成分精确 → 成分 token 包含 → 模糊。

        MIMIC 的药名是自由文本（``Insulin``、``OxyCODONE (Immediate Release)``、
        ``Sodium Chloride 0.9%  Flush``），RXNORM 里对应的是带剂型的规范名
        （``oxycodone``、``sodium chloride 0.9 % flush...``），所以只做字符串精确
        会漏掉一大半——这里逐级放宽，命中级别记录在 ``method`` 里便于复核。
        """
        match = self._lexical("RXNORM", name, cutoff=0.9)
        if match is not None:
            return match

        cleaned = clean_term(name)
        if not cleaned:
            return None
        # 成分精确最可靠（"sodium chloride"、"oxycodone"）
        hit = self._ingredient.get(cleaned)
        if hit is not None:
            return self._match(hit[0], 0.95, "ingredient_exact")
        # 显式别名：dextrose = glucose、senna 制剂 = 番泻叶浓缩物（见 drug_aliases.yaml）
        target = self._aliases.get(cleaned)
        if target:
            hit = self._ingredient.get(target) or self._cleaned.get(target)
            if hit is not None:
                return self._match(hit[0], 0.95, f"alias:{target}")

        # 单一通用名（"dextrose"、"insulin"）容易落到某个剂型上，只认成分表，宁可不映射；
        # 多词名称（"lactated ringers"、"heparin sodium"）才走"去噪声"索引。
        if " " in cleaned:
            hit = self._cleaned.get(cleaned)
            if hit is not None:
                return self._match(hit[0], 0.95, "term_cleaned")

        # 成分词条必须全部出现在输入里（"sodium chloride" ⊆ "sodium chloride flush"）
        tokens = set(cleaned.split())
        candidates: set[str] = set()
        for token in tokens:
            if len(token) >= 3:
                candidates |= self._ingredient_tokens.get(token, set())
        best: tuple[int, str] | None = None
        for key in sorted(candidates):
            key_tokens = set(key.split())
            if key_tokens and key_tokens <= tokens:
                if best is None or len(key_tokens) > best[0]:
                    best = (len(key_tokens), key)
        if best is not None:
            cui, _ = self._ingredient[best[1]]
            return self._match(cui, 0.9, "ingredient_token")

        return self._lexical("RXNORM", cleaned, cutoff=0.88)

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

        candidates = self._fuzzy_candidates(sab, key)
        best = get_close_matches(key, candidates, n=1, cutoff=cutoff)
        if not best:
            self._cache[cache_key] = None
            return None
        score = SequenceMatcher(None, key, best[0]).ratio()
        match = self._match(self._lexicon[sab][best[0]][0], score, "term_fuzzy")
        self._cache[cache_key] = match
        return match


    def _fuzzy_candidates(self, sab: str, key: str) -> list[str]:
        """用最稀有的 token 缩小模糊匹配候选，避免每次扫 20 万条术语。"""
        tokens = [token for token in key.split() if len(token) >= 3]
        sets = [self._token_index[sab].get(token) for token in tokens]
        sets = [item for item in sets if item]
        if sets:
            candidate = min(sets, key=len)
            if 0 < len(candidate) <= 20_000:
                # 排序：set 迭代顺序受哈希随机化影响，不排序会让模糊匹配的平局
                # 在不同进程间抖动（同一份数据跑出不同的 CUI/节点数）
                return sorted(candidate)
        return self._lexicon_keys[sab]


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
