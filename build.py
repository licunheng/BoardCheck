# -*- coding: utf-8 -*-
"""打包脚本:构建单文件 EXE。

运行:
    python3 build.py

输出:
    dist/板检数据核验工具.exe (~50MB)

注意: PyInstaller 不支持中文 exe 名称时会报错,这里用 ASCII 名称。
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SPEC_FILE = PROJECT_ROOT / "agent_project.spec"
OUTPUT_DIR = PROJECT_ROOT / "dist"


def main():
    # 清理旧产物
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    build_dir = PROJECT_ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)

    # 调用 PyInstaller
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(SPEC_FILE),
    ]
    print(" ".join(cmd))
    rc = subprocess.call(cmd, cwd=str(PROJECT_ROOT))
    if rc != 0:
        print("PyInstaller 失败,退出码:", rc)
        return rc

    # 重命名为中文(可选)
    produced = OUTPUT_DIR / ("agent_project.exe" if sys.platform == "win32" else "agent_project")
    final_name = OUTPUT_DIR / "板检数据核验工具.exe"
    if produced.exists() and produced != final_name:
        try:
            produced.rename(final_name)
            print(f"已重命名为: {final_name}")
        except OSError:
            # Windows 下重命名中文可能失败,保留英文名
            print(f"保留原文件名: {produced}")

    if final_name.exists() or produced.exists():
        size_mb = (final_name if final_name.exists() else produced).stat().st_size / 1024 / 1024
        print(f"\n✅ 打包成功: {final_name if final_name.exists() else produced}")
        print(f"   大小: {size_mb:.1f} MB")
    else:
        print("❌ 未找到打包产物")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())