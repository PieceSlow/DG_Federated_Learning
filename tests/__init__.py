"""单元测试包：python -m unittest discover -s tests -t ."""

import sys
from pathlib import Path

# 保证从仓库根目录或任意目录执行 discover 时都能导入包
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
