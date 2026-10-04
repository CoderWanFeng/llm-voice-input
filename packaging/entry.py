"""打包入口：PyInstaller 直接打 main.py 会因相对导入失败，需要一个顶层脚本。"""

import sys

from voice_input.main import main

if __name__ == "__main__":
    sys.exit(main())
