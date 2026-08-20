# -*- coding: utf-8 -*-
"""板检数据核验 Agent - 核心包

用于对比板检日志/数据库 与 追溯平台 CSV,产出三份报告:
- 详细对比表 (xlsx)
- 重传 CSV
- AI 分析报告 (md)

支持可扩展的设备规则插件 (`devices/<device_code>/`),无需修改代码即可适配新设备。
"""

__version__ = "0.1.0"