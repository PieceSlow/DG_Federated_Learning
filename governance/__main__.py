"""python -m governance 入口，等价于 ``python run.py``。"""

import sys

from governance.cli import main

if __name__ == "__main__":
    sys.exit(main())
