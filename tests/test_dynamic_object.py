"""动态新增对象：overlay 合并与校验。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from governance.graph.model import _merge_overlay, load_model
from scripts.propose_object import validate


class OverlayMergeTest(unittest.TestCase):
    def test_merge_adds_object_and_relation(self) -> None:
        base = load_model()
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "objects.local.yaml"
            overlay.write_text(
                yaml.safe_dump(
                    {
                        "objects": [{
                            "name": "LabEvent",
                            "source_table": "labevents",
                            "keys": ["labevent_id"],
                            "attributes": ["itemid", "charttime"],
                            "sensitivity": "L2",
                            "concept": "一次检验结果",
                        }],
                        "relations": [{
                            "name": "has_lab_event",
                            "from": "Encounter",
                            "to": "LabEvent",
                            "on": "hadm_id",
                            "cardinality": "1:N",
                        }],
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            merged = _merge_overlay(base, overlay)
        self.assertIn("LabEvent", merged.objects)
        self.assertIn("has_lab_event", merged.relation_names())
        self.assertEqual(len(merged.objects), len(base.objects) + 1)

    def test_duplicate_name_is_rejected(self) -> None:
        base = load_model()
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "objects.local.yaml"
            overlay.write_text(
                yaml.safe_dump({"objects": [{"name": "Patient", "source_table": "patients",
                                             "keys": ["subject_id"]}]}, allow_unicode=True),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                _merge_overlay(base, overlay)


class ValidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = load_model().objects
        self.snapshot = {
            "labevents": {"rows": 10, "columns": {"labevent_id": "int64", "hadm_id": "Int64",
                                                  "itemid": "int64"}},
        }

    def test_valid_spec(self) -> None:
        errors = validate(self.snapshot, self.base, "labevents", "LabEvent",
                          ["labevent_id"], "L2",
                          {"from": "Encounter", "on": "hadm_id", "to": "LabEvent"})
        self.assertEqual(errors, [])

    def test_unknown_column(self) -> None:
        errors = validate(self.snapshot, self.base, "labevents", "LabEvent",
                          ["not_a_column"], "L2", None)
        self.assertTrue(any("not_a_column" in item for item in errors))

    def test_relation_on_must_be_source_key(self) -> None:
        errors = validate(self.snapshot, self.base, "labevents", "LabEvent",
                          ["labevent_id"], "L2",
                          {"from": "Encounter", "on": "admettime", "to": "LabEvent"})
        self.assertTrue(any("admettime" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
