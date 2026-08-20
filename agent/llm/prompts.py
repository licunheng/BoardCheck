"""Prompt 模板与组装。

四层结构:
1. System Role (固定不变)
2. 设备业务规则 (从 devices/<code>/prompts.md 注入)
3. 输入数据 (当次动态)
4. 输出约束 (JSON Schema 描述)

组装后的 prompt 传给 LLMClient。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.rule_loader import DeviceRules


# === 第 1 层: System Role (固定) ===

SYSTEM_ROLE_BASE = """你是板检数据核验助手,职责是把板检日志中抽取的设备身份字段
(SN、MAC、ICCID、追溯码) 与追溯平台记录逐字段对齐,识别不一致现象。

工作原则:
- 严格按 JSON Schema 输出,不要自由发挥
- 描述现象时使用具体数值,例如"SN 平台值为 0f57f1ca... 而日志中真实 SN 为 FA000001400001..."
- 不要做主观判断,只陈述事实差异
- 警告信息(warnings)放在顶层数组里
"""


# === 第 3 层: 输入数据 (动态) ===

USER_PROMPT_TEMPLATE = """请基于以下输入,完成核验任务。

## 设备信息
- 设备型号: {device_code} ({display_name})
- 是否 4G: {is_4g}
{device_specific_rules}

## 平台 CSV 内容 (前 {n_platform} 条 / 共 {total_platform} 条)
```csv
{platform_csv_preview}
```

## 原始板检数据 (前 {n_raw} 条 / 共 {total_raw} 条)
```text
{raw_data_preview}
```

## 输出要求
严格按照以下 JSON Schema 输出:
```json
{output_schema}
```

请输出 JSON:
"""


# === 第 4 层: 输出约束 (JSON Schema) ===

DEVICE_RECORD_SCHEMA = {
    "type": "object",
    "required": ["device_records", "warnings"],
    "properties": {
        "device_records": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["sn", "mac", "trace_code", "confidence"],
                "properties": {
                    "sn": {"type": "string", "description": "设备 SN(32位hex)"},
                    "mac": {"type": "string", "description": "MAC 地址(12位hex)"},
                    "iccid": {"type": ["string", "null"], "description": "4G ICCID(19-20位)"},
                    "trace_code": {"type": "string", "description": "追溯码(23位)"},
                    "com_port": {"type": "string", "description": "COM 串口号"},
                    "trace_response_time": {"type": "string", "description": "追溯码回复时间 HH:MM:SS"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "notes": {"type": "string"},
                },
            },
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "任何无法解析或异常的警告",
        },
        "stats": {
            "type": "object",
            "description": "本次抽取的统计信息",
        },
    },
}


@dataclass
class AssembledPrompt:
    """组装后的 prompt"""

    system: str
    user: str
    expected_schema: Dict[str, Any]


def assemble_extraction_prompt(
    rules: DeviceRules,
    raw_data_preview: str,
    raw_total_count: int,
    platform_csv_preview: str,
    platform_total_count: int,
    max_raw_lines: int = 500,
) -> AssembledPrompt:
    """组装"字段抽取"任务的 prompt。"""
    # 第 1 层: System Role + 设备专属 4G 提示
    system_parts = [SYSTEM_ROLE_BASE, "", rules.get_4g_indicator()]

    # 第 2 层: 设备专属业务规则(从 prompts.md 抽取关键段落)
    if rules.prompts_md:
        system_parts.append("")
        system_parts.append("## 设备专属规则")
        system_parts.append(_extract_prompts_md_relevant(rules.prompts_md))

    system_prompt = "\n".join(system_parts)

    # 第 3 层: 输入数据
    user_prompt = USER_PROMPT_TEMPLATE.format(
        device_code=rules.device_code,
        display_name=rules.display_name,
        is_4g="是" if rules.is_4g else "否",
        device_specific_rules=_summarize_field_definitions(rules),
        n_platform=min(50, platform_total_count),
        total_platform=platform_total_count,
        platform_csv_preview=platform_csv_preview[:8000],
        n_raw=min(max_raw_lines, raw_total_count),
        total_raw=raw_total_count,
        raw_data_preview=raw_data_preview[:30000],
        output_schema=json.dumps(DEVICE_RECORD_SCHEMA, ensure_ascii=False, indent=2),
    )

    return AssembledPrompt(
        system=system_prompt,
        user=user_prompt,
        expected_schema=DEVICE_RECORD_SCHEMA,
    )


def _extract_prompts_md_relevant(md: str) -> str:
    """从设备 prompts.md 中抽取业务规则段落(去除 frontmatter 与代码块头部)。"""
    # 简单做法:取全文,去掉代码块(避免 prompt 中混入 JSON Schema 模板)
    lines: List[str] = []
    in_code_block = False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            lines.append(line)
    text = "\n".join(lines).strip()
    # 限制长度,避免超长 prompt
    return text[:4000]


def _summarize_field_definitions(rules: DeviceRules) -> str:
    """把 device.json 的字段定义汇总成简洁描述。"""
    fd = rules.field_definitions
    if not fd:
        return ""
    parts = ["### 字段定义"]
    for fname, fdef in fd.items():
        desc = fdef.get("description", "")
        example = fdef.get("example", "")
        parts.append(f"- **{fname}**: {desc} (示例: `{example}`)")
    return "\n".join(parts)