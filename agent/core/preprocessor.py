"""预处理:对文本日志进行行级过滤,剔除无关行,保留含关键字段的行。

整行保留,不切碎(以防 LLM 解读时丢失上下文)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

from ..core.rule_loader import DeviceRules
from .file_reader import RawRecord, detect_file_type, read_raw_file


@dataclass
class PreprocessResult:
    """预处理结果"""

    source_file: Path
    file_type: str
    kept_lines: List[str]  # 文本日志: 保留的行
    kept_records: List[dict]  # SQLite/CSV: 保留的记录列表
    total_input_lines: int  # 输入行数(用于自检:保留率)
    total_kept_lines: int  # 实际保留行数

    @property
    def keep_rate(self) -> float:
        if self.total_input_lines == 0:
            return 0.0
        return self.total_kept_lines / self.total_input_lines


def preprocess_text_log(
    raw_lines: List[str], rules: DeviceRules
) -> List[str]:
    """对文本日志做行级过滤。

    规则:
    - 任何 keep_pattern 命中 → 保留
    - 任何 drop_pattern 命中 → 剔除
    - 都不命中 → 默认保留(保守策略,避免漏掉)
    """
    kept: List[str] = []
    for line in raw_lines:
        if not line or not line.strip():
            continue
        if _should_drop(line, rules):
            continue
        if rules.compiled_keep_patterns:
            if any(p.search(line) for p in rules.compiled_keep_patterns):
                kept.append(line.rstrip() if rules.filter_rules.get("trim_whitespace", True) else line)
        else:
            # 没有 keep_pattern 时,默认保留所有未被 drop_pattern 命中的行
            kept.append(line.rstrip() if rules.filter_rules.get("trim_whitespace", True) else line)
    return kept


def _should_drop(line: str, rules: DeviceRules) -> bool:
    """判断一行是否应被剔除。"""
    return any(p.search(line) for p in rules.compiled_drop_patterns)


def preprocess_raw_file(path: Path, rules: DeviceRules) -> PreprocessResult:
    """读取并预处理一个原始板检数据文件。

    - 文本日志: 按行过滤
    - SQLite/CSV: 直接透传(由后续步骤按表/列规则筛选)
    """
    file_type = detect_file_type(path)
    raw = read_raw_file(path)

    if file_type == "text_log":
        raw_lines = [line if line.endswith("\n") else line + "\n" for line in raw]
        total_input = len(raw_lines)
        kept = preprocess_text_log(raw_lines, rules)
        return PreprocessResult(
            source_file=path,
            file_type=file_type,
            kept_lines=kept,
            kept_records=[],
            total_input_lines=total_input,
            total_kept_lines=len(kept),
        )

    if file_type == "sqlite":
        # SQLite 文件: 返回所有表的记录,由后续步骤选择哪张表
        # raw 应当是一个包含 _tables 的 dict
        assert isinstance(raw, list) and len(raw) == 1 and "_tables" in raw[0]
        all_tables = raw[0]["_tables"]
        # 选择字段最多的表(启发式:最有可能是板检记录表)
        best_table = _pick_best_table(all_tables, rules)
        records = all_tables[best_table] if best_table else []
        return PreprocessResult(
            source_file=path,
            file_type=file_type,
            kept_lines=[],
            kept_records=records,
            total_input_lines=sum(len(v) for v in all_tables.values()),
            total_kept_lines=len(records),
        )

    if file_type == "csv":
        return PreprocessResult(
            source_file=path,
            file_type=file_type,
            kept_lines=[],
            kept_records=raw,  # type: ignore[arg-type]
            total_input_lines=len(raw),
            total_kept_lines=len(raw),
        )

    raise ValueError(f"未实现的文件类型: {file_type}")


def _pick_best_table(tables: Dict[str, list], rules: DeviceRules) -> Optional[str]:
    """从多个表中选择最适合的板检记录表。

    启发式:
    - 包含追溯码/SN/MAC 等关键词的列
    - 记录数较多
    """
    if not tables:
        return None

    keywords = {"追溯码", "trace_code", "trace", "sn", "mac", "uuid"}
    best_score = -1
    best_name: Optional[str] = None

    for table_name, rows in tables.items():
        if not rows:
            continue
        # 取第一行检查字段名
        columns = set(rows[0].keys())
        match_count = sum(1 for k in columns if any(kw in k.lower() for kw in keywords))
        score = match_count * 100 + len(rows)
        if score > best_score:
            best_score = score
            best_name = table_name

    return best_name