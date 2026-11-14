"""配置与路径解析的测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from governance import config
from governance.config import Settings


class DefaultsTest(unittest.TestCase):
    def test_repo_root_contains_governance_package(self) -> None:
        self.assertTrue((config.repo_root() / "governance" / "pipeline.py").exists())

    def test_meds_src_points_to_vendored_copy(self) -> None:
        self.assertEqual(config.meds_src(), config.repo_root() / "third_party" / "meds" / "src")
        self.assertTrue((config.meds_src() / "meds" / "schema.py").exists())

    def test_env_overrides(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "MIMIC_ROOT": "D:/data/mimic",
                "MIMIC_NOTE_ROOT": "D:/data/note",
                "GOVERNANCE_OUTPUT": "D:/out",
            },
        ):
            self.assertEqual(config.default_mimic_root(), Path("D:/data/mimic"))
            self.assertEqual(config.default_note_root(), Path("D:/data/note"))
            self.assertEqual(config.default_output(), Path("D:/out"))


class SettingsTest(unittest.TestCase):
    def test_directory_layout(self) -> None:
        settings = Settings(mimic_root=Path("m"), note_root=Path("n"), output=Path("out"))
        self.assertEqual(settings.eventstore, Path("out/eventstore"))
        self.assertEqual(settings.governance, Path("out/governance"))
        self.assertEqual(settings.audit_dir, Path("out/audit"))
        self.assertEqual(settings.cohort_size, 100)
        self.assertEqual(settings.max_notes_per_subject, 3)

    def test_ensure_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(mimic_root=Path("m"), note_root=Path("n"), output=Path(tmp))
            settings.ensure_dirs()
            for sub in ("eventstore/data", "governance/dataset", "governance/mapping",
                        "governance/metadata", "governance/quality", "governance/classification",
                        "governance/catalog", "audit"):
                self.assertTrue((Path(tmp) / sub).is_dir(), sub)


class ResolveDataFileTest(unittest.TestCase):
    def test_prefers_csv_then_gz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "hosp").mkdir()
            (base / "hosp" / "patients.csv.gz").touch()
            self.assertEqual(
                config.resolve_data_file(base, "hosp", "patients").name, "patients.csv.gz"
            )
            (base / "hosp" / "patients.csv").touch()
            self.assertEqual(config.resolve_data_file(base, "hosp", "patients").name, "patients.csv")

    def test_missing_file_gives_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as ctx:
                config.resolve_data_file(Path(tmp), "hosp", "patients")
            self.assertIn("mimic-root", str(ctx.exception))


class RequirePathsTest(unittest.TestCase):
    def test_require_mimic_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                config.require_mimic_root(Path(tmp))
            (Path(tmp) / "hosp").mkdir()
            self.assertEqual(config.require_mimic_root(Path(tmp)), Path(tmp))

    def test_require_note_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                config.require_note_root(Path(tmp))
            (Path(tmp) / "note").mkdir()
            (Path(tmp) / "note" / "discharge.csv.gz").touch()
            self.assertEqual(config.require_note_root(Path(tmp)), Path(tmp))

    def test_require_meds_src(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                config.require_meds_src(Path(tmp))


if __name__ == "__main__":
    unittest.main()
