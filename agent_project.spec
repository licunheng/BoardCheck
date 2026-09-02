# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

打包为单文件 EXE,无 Python 依赖,产线工人可直接运行。

构建命令:
    pyinstaller agent_project.spec

产物:
    dist/agent_project.exe (~50MB,含 Python 运行时 + Flask + openpyxl + requests)
"""

import sys
from pathlib import Path

block_cipher = None

# 项目根
PROJECT_ROOT = Path('.').resolve()

# 数据文件:把所有 agent 内部文件打包进去(devices 目录、static 目录、rules 模板)
datas = [
    # 设备规则(可在不重新打包的情况下被用户替换)
    (str(PROJECT_ROOT / 'agent' / 'devices'), 'agent/devices'),
    (str(PROJECT_ROOT / 'agent' / 'static'), 'agent/static'),
    (str(PROJECT_ROOT / 'agent' / 'rules'), 'agent/rules'),
]

# 隐藏导入
hiddenimports = [
    'flask',
    'openpyxl',
    'xlrd',
    'requests',
    'sqlite3',
    'json',
    're',
    'csv',
    'pathlib',
    'dataclasses',
    'logging',
    'webbrowser',
    'uuid',
    'threading',
]

a = Analysis(
    ['run_agent.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除大依赖以减小体积
        'numpy', 'pandas', 'matplotlib', 'scipy', 'PIL',
        'pytest', 'sphinx', 'jupyter',
        'tkinter', 'PyQt5', 'PySide2',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='板检数据核验工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 控制台模式(显示启动信息)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)