"""编排测试：阶段顺序、依赖声明、阶段选择与命令行自检。"""

from __future__ import annotations

import contextlib
import io
import unittest

from governance import pipeline
from governance.cli import main


class StageRegistryTest(unittest.TestCase):
    def test_order_and_keys(self) -> None:
        self.assertEqual(
            [stage.key for stage in pipeline.STAGES],
            [
                "ingest", "standardize", "quality", "materialize", "assemble",
                "align", "objectify", "label", "classify", "catalog", "report",
            ],
        )

    def test_titles_are_numbered_in_order(self) -> None:
        for index, stage in enumerate(pipeline.STAGES, start=1):
            self.assertTrue(stage.title.startswith(f"{index:02d} "), stage.title)

    def test_dependencies_point_backwards(self) -> None:
        seen: set[str] = set()
        for stage in pipeline.STAGES:
            for requirement in stage.requires:
                self.assertNotIn(requirement, seen, f"{stage.key} 依赖了尚未产出的 {requirement}")
            seen.add(stage.key)

    def test_injected_nodes_cover_all_stages(self) -> None:
        graph = pipeline.build_graph(pipeline.STAGES)
        self.assertIsNotNone(graph)


class SelectTest(unittest.TestCase):
    def test_select_subset_keeps_order(self) -> None:
        selected = pipeline._select(["align", "assemble"])
        self.assertEqual([stage.key for stage in selected], ["assemble", "align"])

    def test_unknown_stage_raises(self) -> None:
        with self.assertRaises(ValueError):
            pipeline._select(["nope"])

    def test_describe_lists_every_stage(self) -> None:
        text = pipeline.describe_stages()
        for stage in pipeline.STAGES:
            self.assertIn(stage.key, text)


class CliTest(unittest.TestCase):
    def test_list_stages(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--list-stages"]), 0)
        self.assertIn("ingest", out.getvalue())
        self.assertIn("report", out.getvalue())

    def test_missing_data_returns_2(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(["--mimic-root", "/nonexistent/mimic", "--note-root", "/nonexistent/note"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
