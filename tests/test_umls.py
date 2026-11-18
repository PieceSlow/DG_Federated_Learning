"""UMLS 概念层测试：微缩 RRF 夹具（不接触 13 GB 源文件）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from governance import pipeline
from governance.concept import run as concept_run
from governance.config import Settings
from governance.context import GovernanceContext
from governance.umls.config import cache_root
from governance.umls.link import ConceptLinker
from governance.umls.preprocess import preprocess
from tests.synthetic import write_synthetic_mimic

CONSO_FIELDS = 18
STY_FIELDS = 6
REL_FIELDS = 16


def _conso(cui: str, lat: str, ispref: str, sab: str, tty: str, code: str,
           text: str, suppress: str = "N") -> str:
    fields = [""] * CONSO_FIELDS
    fields[0] = cui
    fields[1] = lat
    fields[2] = "P"
    fields[3] = f"L{cui}"
    fields[4] = "PF"
    fields[5] = f"S{cui}"
    fields[6] = ispref
    fields[7] = f"A{cui}"
    fields[11] = sab
    fields[12] = tty
    fields[13] = code
    fields[14] = text
    fields[15] = "0"
    fields[16] = suppress
    return "|".join(fields)


def _sty(cui: str, tui: str, text: str) -> str:
    fields = [""] * STY_FIELDS
    fields[0], fields[1], fields[2], fields[3], fields[4] = cui, tui, f"D{tui}", text, f"AT{cui}"
    return "|".join(fields)


def _rel(cui1: str, rel: str, cui2: str, rela: str, sab: str) -> str:
    fields = [""] * REL_FIELDS
    fields[0], fields[1], fields[2] = cui1, f"A{cui1}", "C"
    fields[3], fields[4], fields[5], fields[6] = rel, cui2, f"A{cui2}", "C"
    fields[7], fields[8], fields[10], fields[14] = rela, f"R{cui1}{cui2}", sab, "N"
    return "|".join(fields)


def write_fixture(root: Path) -> Path:
    meta = root / "META"
    meta.mkdir(parents=True)
    conso = [
        _conso("C001", "ENG", "Y", "ICD10CM", "PT", "I10", "Essential hypertension"),
        _conso("C001", "ENG", "N", "ICD9CM", "PT", "4019", "Essential hypertension, unspecified"),
        _conso("C002", "ENG", "Y", "ICD10CM", "PT", "E119", "Type 2 diabetes mellitus"),
        _conso("C003", "ENG", "Y", "RXNORM", "IN", "1191", "Aspirin"),
        _conso("C004", "ENG", "Y", "LNC", "LN", "2160-0", "Creatinine [Mass/volume] in Serum or Plasma"),
        _conso("C004", "ENG", "N", "LNC", "SY", "2160-0", "Creatinine"),
        _conso("C005", "ENG", "Y", "ICD10PCS", "PT", "0BH17EZ", "Insertion of endotracheal airway"),
        _conso("C006", "ENG", "Y", "ICD9CM", "PT", "3893", "Venous catheterization"),
        # 以下应被过滤：非英文、被抑制、非目标词表
        _conso("C007", "SPA", "Y", "ICD10CM", "PT", "I10", "Hipertension esencial"),
        _conso("C008", "ENG", "Y", "ICD10CM", "PT", "X1", "Suppressed concept", suppress="Y"),
        _conso("C009", "ENG", "Y", "MTHSPL", "PT", "Y1", "Other vocabulary"),
    ]
    (meta / "MRCONSO.RRF").write_text("\n".join(conso) + "\n", encoding="utf-8")
    sty = [
        _sty("C001", "T047", "Disease or Syndrome"),
        _sty("C002", "T047", "Disease or Syndrome"),
        _sty("C003", "T121", "Pharmacologic Substance"),
        _sty("C004", "T059", "Laboratory Procedure"),
        _sty("C005", "T061", "Therapeutic or Preventive Procedure"),
    ]
    (meta / "MRSTY.RRF").write_text("\n".join(sty) + "\n", encoding="utf-8")
    rel = [
        _rel("C003", "RO", "C001", "may_treat", "RXNORM"),
        _rel("C001", "RO", "C010", "may_treat", "RXNORM"),  # C010 不在概念集合，应过滤
        _rel("C001", "PAR", "C002", "", "SNOMEDCT_US"),
    ]
    (meta / "MRREL.RRF").write_text("\n".join(rel) + "\n", encoding="utf-8")
    return meta


class PreprocessTest(unittest.TestCase):
    def test_filter_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = write_fixture(Path(tmp))
            cache = Path(tmp) / "cache"
            manifest = preprocess(meta, cache)
            self.assertEqual(manifest["concepts"], 6)
            self.assertEqual(manifest["terms"], 8)
            self.assertEqual(manifest["relations"], 2)
            for name in ("terms.parquet", "concepts.parquet", "sty.parquet", "rel.parquet"):
                self.assertTrue((cache / name).exists(), name)
            self.assertTrue(json.loads((cache / "manifest.json").read_text(encoding="utf-8")))

            # 命中缓存：第二次不再重建
            again = preprocess(meta, cache)
            self.assertEqual(again["generated_at"], manifest["generated_at"])


class LinkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        meta = write_fixture(Path(cls._tmp.name))
        cache = Path(cls._tmp.name) / "cache"
        preprocess(meta, cache)
        cls.linker = ConceptLinker(cache)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_icd10_exact(self) -> None:
        match = self.linker.map_icd(10, "I10")
        self.assertIsNotNone(match)
        self.assertEqual(match.cui, "C001")
        self.assertEqual(match.method, "icd_exact")
        self.assertIn("Disease", match.sty)

    def test_icd9_exact(self) -> None:
        match = self.linker.map_icd(9, "4019")
        self.assertIsNotNone(match)
        self.assertEqual(match.cui, "C001")

    def test_procedure_exact(self) -> None:
        match = self.linker.map_procedure(10, "0BH17EZ")
        self.assertIsNotNone(match)
        self.assertEqual(match.cui, "C005")

    def test_drug_exact_and_fuzzy(self) -> None:
        exact = self.linker.map_drug("Aspirin")
        self.assertEqual(exact.cui, "C003")
        fuzzy = self.linker.map_drug("aspirn")
        self.assertIsNotNone(fuzzy)
        self.assertEqual(fuzzy.method, "term_fuzzy")

    def test_lab_exact(self) -> None:
        match = self.linker.map_lab("Creatinine [Mass/volume] in Serum or Plasma")
        self.assertEqual(match.cui, "C004")

    def test_unmapped_returns_none(self) -> None:
        self.assertIsNone(self.linker.map_icd(10, "ZZZ"))
        self.assertIsNone(self.linker.map_drug("not-a-drug"))


class ConceptStageTest(unittest.TestCase):
    def _context(self, tmp: Path, with_cache: bool) -> GovernanceContext:
        settings = Settings(mimic_root=tmp / "m", note_root=tmp / "n", output=tmp / "out")
        ctx = GovernanceContext(settings=settings)
        ctx.tables = {
            "diagnoses_icd": pd.DataFrame(
                {"subject_id": [1, 1], "hadm_id": [11, 11], "seq_num": [1, 2],
                 "icd_code": ["I10", "UNKNOWN"], "icd_version": [10, 10]}
            ),
            "procedures_icd": pd.DataFrame(
                {"subject_id": [1], "hadm_id": [11], "seq_num": [1],
                 "icd_code": ["0BH17EZ"], "icd_version": [10]}
            ),
            "prescriptions": pd.DataFrame({"drug": ["Aspirin"]}),
            "d_labitems": pd.DataFrame({"itemid": [50912], "label": ["Creatinine [Mass/volume] in Serum or Plasma"]}),
        }
        if with_cache:
            meta = write_fixture(tmp)
            preprocess(meta, cache_root(settings.output))
        return ctx

    def test_mapping_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = self._context(root, with_cache=True)
            concept_run(ctx)

            map_path = ctx.settings.governance / "metadata" / "concept_map.parquet"
            self.assertTrue(map_path.exists())
            frame = pd.read_parquet(map_path)
            self.assertEqual(len(frame), 4)  # I10 / 0BH17EZ / Aspirin / Creatinine
            self.assertEqual(int((frame["source_system"] == "RXNORM").sum()), 1)

            report = json.loads(
                (ctx.settings.governance / "quality" / "concept_coverage.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["overall"]["mapped"], 4)
            self.assertEqual(report["sources"]["ICD10"]["total"], 2)
            self.assertEqual(ctx.issue_counts.get("CONCEPT_UNMAPPED|warning"), 1)

    def test_missing_cache_skips_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._context(Path(tmp), with_cache=False)
            concept_run(ctx)
            self.assertEqual(ctx.stats["concept_mapping"]["status"], "skipped")


class PipelineConceptTest(unittest.TestCase):
    def test_pipeline_with_umls_cache_builds_concept_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mimic = write_synthetic_mimic(root)
            settings = Settings(mimic_root=mimic, note_root=root, output=root / "out", cohort_size=3)
            meta = write_fixture(root)
            preprocess(meta, cache_root(settings.output))

            ctx = pipeline.run(settings)
            self.assertEqual(ctx.stats["concept_mapping"]["overall"]["mapped"], 7)

            manifest = json.loads(
                (settings.governance / "graph" / "graph_manifest.json").read_text(encoding="utf-8")
            )
            # 命中 UMLS 的编码按 CUI 合并，没命中的编码也建编码级节点（兜底），
            # 所以节点数 = CUI 节点 + 编码级节点，不再是"只保留映射成功的"。
            nodes = pd.read_parquet(settings.governance / "graph" / "nodes.parquet")
            concept_nodes = set(nodes.loc[nodes.object_type == "Concept", "object_id"])
            self.assertEqual(manifest["objects"]["Concept"], len(concept_nodes))
            self.assertTrue(any(node.startswith("Concept:C") for node in concept_nodes))
            self.assertTrue(any(not node.startswith("Concept:C") for node in concept_nodes))
            # 有编码的事件节点都要有概念边（命中 CUI 或编码级兜底），不再漏未映射的编码；
            # 缺编码的处方（synthetic 里故意留了一条）本来就没有概念可连。
            code_field = {
                "Diagnosis": "icd_code",
                "Prescription": "formulary_drug_cd",
                "Procedure": "icd_code",
            }
            for object_type, relation in (
                ("Diagnosis", "diagnosis_concept"),
                ("Prescription", "prescription_concept"),
                ("Procedure", "procedure_concept"),
            ):
                subset = nodes[nodes.object_type == object_type]
                with_code = sum(
                    1
                    for raw_attributes in subset.attributes
                    if json.loads(raw_attributes or "{}").get(code_field[object_type]) not in (None, "")
                )
                self.assertEqual(
                    manifest["relations"][relation],
                    with_code,
                    msg=f"{relation} 应等于「有编码的 {object_type} 节点数」",
                )

            # 标签里的每个概念对象，除文书（概念表里没有）外，必须能在图谱里找到
            object_labels = pd.read_parquet(
                settings.governance / "labels" / "object_labels.parquet"
            )
            label_concepts = set(
                object_labels.loc[object_labels.object_type == "Concept", "object_id"]
            )
            self.assertEqual(label_concepts - concept_nodes, {"Concept:MIMIC_NOTE:DISCHARGE"})

            labels = json.loads(
                (settings.governance / "metadata" / "labels.json").read_text(encoding="utf-8")
            )
            self.assertTrue(labels["concept"]["umls_enabled"])
            self.assertTrue(labels["concept"]["semantic_type_counts"])


if __name__ == "__main__":
    unittest.main()
