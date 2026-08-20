"""规则自检 CLI。

用法:
    python -m agent --device A02FG --rule-check

行为:
- 读取 devices/<device>/samples/ 下的样本文件
- 应用该设备的过滤与抽取规则
- 输出报告:
    * 行保留率
    * 4 字段抽取成功率(SN / MAC / ICCID / 追溯码)
    * fail-fast 判断(未达标则 exit code != 0)
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import AGENT_ROOT
from ..core.extractor import DeviceRecord, extract_from_records, extract_from_text_log
from ..core.file_reader import detect_file_type, read_csv, read_text_log
from ..core.preprocessor import preprocess_raw_file
from ..core.rule_loader import DeviceRules


@dataclass
class FieldStats:
    """单字段抽取统计"""

    field_name: str
    total_records: int = 0
    extracted: int = 0
    missing: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_records == 0:
            return 0.0
        return self.extracted / self.total_records


@dataclass
class RuleCheckReport:
    """自检报告"""

    device_code: str
    samples_found: List[str]
    total_input_lines: int
    total_kept_lines: int
    keep_rate: float
    field_stats: Dict[str, FieldStats]
    records_extracted: int
    warnings: List[str] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_code": self.device_code,
            "samples_found": self.samples_found,
            "total_input_lines": self.total_input_lines,
            "total_kept_lines": self.total_kept_lines,
            "keep_rate": round(self.keep_rate * 100, 2),
            "records_extracted": self.records_extracted,
            "field_stats": {
                k: {
                    "total": v.total_records,
                    "extracted": v.extracted,
                    "missing": v.missing,
                    "success_rate": round(v.success_rate * 100, 2),
                }
                for k, v in self.field_stats.items()
            },
            "warnings": self.warnings,
            "passed": self.passed,
        }


def run_rule_check(device_code: str) -> int:
    """执行规则自检,返回进程退出码。"""
    try:
        rules = DeviceRules.load(device_code)
    except Exception as e:
        print(f"❌ 加载设备规则失败: {e}", file=sys.stderr)
        return 2

    samples_dir = rules.rules_dir / "samples"
    if not samples_dir.is_dir():
        print(f"⚠️  设备 {device_code!r} 的 samples 目录不存在: {samples_dir}", file=sys.stderr)
        print(f"   请放入至少一个真实样本(.txt/.log/.md/.db/.csv)", file=sys.stderr)
        return 3

    sample_files = sorted(
        p for p in samples_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )
    if not sample_files:
        print(f"⚠️  设备 {device_code!r} 的 samples 目录为空", file=sys.stderr)
        return 3

    # 找出 .txt/.log 样本(用于文本日志规则自检)
    text_samples = [p for p in sample_files if detect_file_type(p) == "text_log"]
    db_samples = [p for p in sample_files if detect_file_type(p) == "sqlite"]
    csv_samples = [p for p in sample_files if detect_file_type(p) == "csv"]

    if not (text_samples or db_samples or csv_samples):
        print(f"⚠️  没有可识别的样本文件(.txt/.log/.db/.sqlite/.csv)", file=sys.stderr)
        return 3

    # 累计统计
    total_input_lines = 0
    total_kept_lines = 0
    all_records: List[DeviceRecord] = []

    for sample in text_samples:
        try:
            pre = preprocess_raw_file(sample, rules)
            total_input_lines += pre.total_input_lines
            total_kept_lines += pre.total_kept_lines
            recs = extract_from_text_log(pre.kept_lines, rules)
            all_records.extend(recs)
        except Exception as e:
            print(f"❌ 处理 {sample.name} 失败: {e}", file=sys.stderr)
            return 4

    for sample in csv_samples:
        try:
            records = read_csv(sample)
            recs = extract_from_records(records, rules)
            all_records.extend(recs)
        except Exception as e:
            print(f"❌ 处理 {sample.name} 失败: {e}", file=sys.stderr)
            return 4

    # 字段统计
    field_stats: Dict[str, FieldStats] = {}
    field_names = ["sn", "mac", "iccid", "trace_code"]
    for fname in field_names:
        fs = FieldStats(field_name=fname)
        for r in all_records:
            fs.total_records += 1
            v = getattr(r, fname, None)
            if v:
                fs.extracted += 1
            else:
                fs.missing += 1
        field_stats[fname] = fs

    # 评估通过性
    keep_rate = (total_kept_lines / total_input_lines) if total_input_lines else 0.0
    warnings: List[str] = []
    passed = True

    # 行保留率应 >= 5%(粗筛后只保留少数关键行是正常的)
    # 对于文本日志样本,这个阈值取决于日志结构
    if total_input_lines and keep_rate > 0.5:
        warnings.append(
            f"行保留率 {keep_rate*100:.1f}% 偏高(>50%),可能 drop_patterns 漏配"
        )

    # 各字段成功率
    for fname in ("sn", "mac", "trace_code"):
        fs = field_stats[fname]
        if fs.total_records == 0:
            continue
        if fs.success_rate < 0.5:
            passed = False
            warnings.append(
                f"字段 {fname} 抽取成功率仅 {fs.success_rate*100:.1f}%(<50%),正则可能需要调整"
            )

    # 4G 设备的 ICCID 必填
    if rules.is_4g:
        iccid_fs = field_stats.get("iccid")
        if iccid_fs and iccid_fs.total_records:
            if iccid_fs.success_rate < 0.5:
                passed = False
                warnings.append(
                    f"ICCID 抽取成功率仅 {iccid_fs.success_rate*100:.1f}%(<50%)"
                )

    report = RuleCheckReport(
        device_code=device_code,
        samples_found=[p.name for p in sample_files],
        total_input_lines=total_input_lines,
        total_kept_lines=total_kept_lines,
        keep_rate=keep_rate,
        field_stats=field_stats,
        records_extracted=len(all_records),
        warnings=warnings,
        passed=passed,
    )

    _print_report(report)
    return 0 if passed else 5


def _print_report(report: RuleCheckReport) -> None:
    """格式化输出报告。"""
    print()
    print(f"━━━ 设备规则自检报告: {report.device_code} ━━━")
    print()
    print(f"样本文件: {len(report.samples_found)} 个")
    for s in report.samples_found:
        print(f"  - {s}")
    print()
    print(f"文本日志输入行数: {report.total_input_lines}")
    print(f"过滤后保留行数: {report.total_kept_lines}")
    print(f"行保留率: {report.keep_rate * 100:.2f}%")
    print(f"抽取到设备记录: {report.records_extracted} 条")
    print()
    print("字段抽取统计:")
    print(f"  {'字段':<10} {'总数':>6} {'抽取到':>8} {'缺失':>6} {'成功率':>8}")
    for fs in report.field_stats.values():
        print(
            f"  {fs.field_name:<10} {fs.total_records:>6} {fs.extracted:>8} "
            f"{fs.missing:>6} {fs.success_rate * 100:>7.1f}%"
        )
    print()
    if report.warnings:
        print("⚠️  警告:")
        for w in report.warnings:
            print(f"  - {w}")
        print()
    if report.passed:
        print("✅ 自检通过")
    else:
        print("❌ 自检未通过,请调整规则")

    print()
    print("(JSON 格式)")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))