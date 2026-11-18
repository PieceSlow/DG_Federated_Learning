"""分级级别配置：levels.yaml 是单一来源。"""

from __future__ import annotations

import unittest

import governance.levels as levels


class LevelConfigTest(unittest.TestCase):
    def test_reads_yaml(self) -> None:
        self.assertEqual(levels.level_of("C08"), "L3")
        self.assertEqual(levels.level_of("C03"), "L2")

    def test_fallback(self) -> None:
        self.assertEqual(levels.level_of("C99", default="L2"), "L2")

    def test_override_takes_effect(self) -> None:
        original = levels._load
        levels._load = lambda: {"C08": "L2"}
        try:
            self.assertEqual(levels.level_of("C08"), "L2")
        finally:
            levels._load = original


if __name__ == "__main__":
    unittest.main()
