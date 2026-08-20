# -*- coding: utf-8 -*-
"""Agent 启动入口(供 PyInstaller 打包使用)。

执行 `python -m agent` 时实际是 agent/__main__.py,但 PyInstaller 打包成 EXE
时,需要这个外部入口文件,避免 `from .core.xx` 相对导入失败。
"""

import os
import sys

# 强制 UTF-8 输出(避免 Windows cmd 的 GBK 编码报错)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 把项目根加入 sys.path,这样可以像 import 包一样使用 agent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.__main__ import main

if __name__ == "__main__":
    sys.exit(main())