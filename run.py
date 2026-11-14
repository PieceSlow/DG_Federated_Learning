"""医疗多源数据治理流水线入口。

用法::

    python run.py                                  # 全流程，默认 100 人队列
    python run.py --cohort-size 20                 # 小队列试跑
    python run.py --stages assemble,align          # 只重跑指定阶段
    python run.py --list-stages                    # 查看阶段与依赖

产物写入 ``output/``（事件仓储 eventstore/、治理资产 governance/、审计 audit/），
该目录已在 .gitignore 中排除：其中含患者级派生数据，不得提交或外传。
"""

import sys

from governance.cli import main

if __name__ == "__main__":
    sys.exit(main())
