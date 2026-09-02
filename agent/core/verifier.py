"""端到端核验编排器:串联预处理 → 抽取 → 比对 → 报告生成。

可被 Web UI 与命令行共用。
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .comparator import ComparisonRow, compare_records, summarize
from .extractor import (
    DeviceRecord,
    extract_from_records,
    extract_from_text_log,
    extract_with_llm_fallback,
)
from .file_reader import read_spreadsheet
from .llm_review import llm_reconcile_extraction, llm_review_comparison, llm_review_conflicts
from .preprocessor import PreprocessResult, preprocess_raw_file
from .quality import dedupe_and_validate
from .rule_loader import DeviceRules

logger = logging.getLogger(__name__)

# 字段 → 平台 CSV 列名候选(按优先级降序,越靠前越可信)。
# 用于列名映射匹配不上时的模糊匹配自动纠错。
_FIELD_SYNONYMS: Dict[str, List[str]] = {
    "sn": ["设备编号", "sn", "序列号", "id","序列碼", "serial", "写入sn", "uuid"],
    "mac": ["蓝牙mac", "mac", "mac地址", "蓝牙mac地址", "blemac", "蓝牙地址"],
    "iccid": ["4g信息", "iccid", "4g卡号", "sim卡", "sim", "卡号", "iccid卡号"],
    "trace_code": ["追溯码", "tracecode", "trace", "溯源码", "追溯碼", "追溯"],
}

# 模糊匹配置信度阈值:低于该值不自动纠错,仍报错提示人工确认。
_FUZZY_THRESHOLD = 0.75


def _norm_col(name: str) -> str:
    """列名归一化:去空白/下划线/连字符/冒号,转小写。"""
    return re.sub(r"[\s_\-:：.]", "", name or "").lower()


def _column_similarity(field: str, configured: str, candidate: str) -> float:
    """计算 candidate 作为 field 正确平台列的置信度(0~1)。

    综合两个信号:
    1. 语义同义词表(越靠前的同义词权重越高)
    2. 与已配置列名的文本相似度(能捕捉拼写变体)
    """
    c = _norm_col(candidate)
    cfg = _norm_col(configured) if configured else ""
    best = 0.0
    for i, syn in enumerate(_FIELD_SYNONYMS.get(field, [])):
        s = _norm_col(syn)
        if not s:
            continue
        base = 1.0 - 0.02 * i  # 同义词越靠前权重越高
        if c == s:
            best = max(best, base)
        elif c and (s in c or c in s):
            best = max(best, base * 0.9)
    if cfg:
        best = max(best, difflib.SequenceMatcher(None, cfg, c).ratio())
    return best


@dataclass
class VerificationResult:
    """核验结果(内存表示)"""

    device_code: str
    raw_file: Path
    platform_csv: Path
    output_dir: Path

    raw_records: List[DeviceRecord]
    comparison_rows: List[ComparisonRow]
    summary: Dict[str, int]

    # 输出路径
    xlsx_path: Optional[Path] = None
    csv_path: Optional[Path] = None
    md_path: Optional[Path] = None

    @property
    def total_count(self) -> int:
        return self.summary.get("total", 0)

    @property
    def inconsistent_count(self) -> int:
        return self.summary.get("mismatch", 0) + self.summary.get("raw_only", 0) + self.summary.get("platform_only", 0)


def run_verification(
    device_code: str,
    raw_path: Union[Path, List[Path]],
    platform_csv_path: Union[Path, List[Path]],
    output_dir: Path,
    llm_review: Optional[bool] = None,
    independent_extraction: Optional[bool] = None,
    comparison_review: Optional[bool] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """执行一次完整核验流程。

    raw_path / platform_csv_path 支持单个 Path 或多个 Path 列表:
    - 多个板检日志(txt)会合并为一份输入
    - 多个平台 CSV 会合并为一份平台记录集

    LLM 复核开关(人工可控):
    - llm_review: 主开关。None=回退设备配置默认;False=关;True=开
    - independent_extraction: 阶段一(独立抽取交叉验证,很慢)。llm_review 开时默认关
    - comparison_review: 阶段二(不一致记录复核,较快)。llm_review 开时默认开

    Returns:
        包含报告路径与统计信息的字典。
    """
    started = time.time()
    rules = DeviceRules.load(device_code)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_paths = _as_path_list(raw_path)
    platform_paths = _as_path_list(platform_csv_path)

    # 解析 LLM 复核开关
    indep_enabled, comp_enabled = _resolve_review_switches(
        llm_review, independent_extraction, comparison_review
    )

    def _report(msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    # 1. 读取并预处理原始数据
    _report("预处理原始数据…")
    logger.info("Step 1: 预处理原始数据 (%d 个文件)", len(raw_paths))
    preprocessed = _preprocess_raw_files(raw_paths, rules)
    logger.info(
        "  → 文件类型=%s, 输入=%d, 保留=%d, 保留率=%.1f%%",
        preprocessed.file_type,
        preprocessed.total_input_lines,
        preprocessed.total_kept_lines,
        preprocessed.keep_rate * 100,
    )

    # 2. 抽取字段(文本日志按文件分别抽取,避免跨日时间窗错绑)
    _report("字段抽取…")
    logger.info("Step 2: 字段抽取")
    if preprocessed.file_type == "text_log":
        raw_records, file_kept_lines, total_in, total_kept = _extract_text_logs_per_file(
            raw_paths, rules
        )
        # 同步累计统计到 preprocessed,后续日志使用 file_kept_lines(全局索引版)
        preprocessed.kept_lines = file_kept_lines
        preprocessed.total_input_lines = total_in
        preprocessed.total_kept_lines = total_kept
        logger.info(
            "  → 文本日志按文件独立抽取(%d 个文件,累计保留 %d 行,合并 %d 条记录)",
            len(raw_paths),
            total_kept,
            len(raw_records),
        )
        # LLM 兜底:补全正则未抽全的记录(需配置 API Key)
        if (rules.extraction_rules.get("llm_fallback", {}) or {}).get("enabled", False):
            raw_records = extract_with_llm_fallback(
                file_kept_lines, raw_records, rules
            )
    elif preprocessed.file_type in ("sqlite", "csv"):
        raw_records = extract_from_records(preprocessed.kept_records, rules)
        file_kept_lines = preprocessed.kept_lines
    else:
        raise ValueError(f"未支持的文件类型: {preprocessed.file_type}")
    logger.info("  → 抽取到 %d 条设备记录", len(raw_records))

    # 2.5 LLM 复核:独立抽取 + 交叉验证(修正可能的错绑,比对之前执行)
    llm_meta: Dict[str, Any] = {}
    if preprocessed.file_type == "text_log":
        _report("LLM 独立抽取交叉验证…" if indep_enabled else "LLM 独立抽取已关闭,跳过")
        raw_records, extract_meta = llm_reconcile_extraction(
            rules, preprocessed.kept_lines, raw_records, enabled=indep_enabled
        )
        if extract_meta.get("enabled"):
            llm_meta["extraction"] = extract_meta
            logger.info("  → LLM 交叉验证: %s", extract_meta)

    # 2.6 数据质量层:归一去重 + 1:1 唯一性校验(纯确定性)
    _report("数据质量校验(去重/唯一性)…")
    raw_records, quality_report = dedupe_and_validate(raw_records, rules)
    logger.info(
        "  → 数据质量: 去重 %d 条, 冲突 %d 处, ICCID 长度异常 %d 条",
        quality_report["deduped"],
        quality_report["conflicts"],
        quality_report["truncated_iccid"],
    )

    # 2.7 LLM 定向复核:对低置信度/冲突记录,喂锚点窗口判真值(硬约束仲裁后才采纳)
    if preprocessed.file_type == "text_log":
        _report("LLM 不确定记录定向复核…" if indep_enabled else "LLM 不确定记录复核已关闭,跳过")
        raw_records, conflict_meta = llm_review_conflicts(
            rules, preprocessed.kept_lines, raw_records, enabled=indep_enabled
        )
        if conflict_meta.get("enabled"):
            llm_meta["conflict_review"] = conflict_meta
            logger.info("  → LLM 定向复核: %s", conflict_meta)
            # 复核可能改字段 → 再过一次质量层去重/唯一性,把修正后的值纳入比对
            raw_records, quality_report2 = dedupe_and_validate(raw_records, rules)
            if quality_report2.get("deduped") or quality_report2.get("conflicts"):
                logger.info("  → 复核后质量重校验: %s", quality_report2)
                quality_report = quality_report2

    # 3. 读取平台 CSV
    _report("读取平台记录…")
    logger.info("Step 3: 读取平台 CSV (%d 个文件)", len(platform_paths))
    platform_records_raw = _read_platform_csvs(platform_paths)
    # 3.1 解析平台列名:精确匹配优先,匹配不上时模糊匹配自动纠错
    resolved_cols, col_warnings = _resolve_platform_columns(platform_records_raw, rules)
    for w in col_warnings:
        logger.warning("平台列名: %s", w)
    if resolved_cols and _has_missing_required(resolved_cols, rules):
        raise ValueError(
            "平台 CSV 列名映射有误,无法自动匹配。请检查该设备的 platform_csv_columns 配置。"
            + " | ".join(col_warnings)
        )
    platform_records = _normalize_platform_records(platform_records_raw, rules, resolved_cols)
    logger.info("  → 平台记录 %d 条", len(platform_records))

    # 4. 比对
    _report("比对原始 vs 平台…")
    logger.info("Step 4: 比对原始 vs 平台")
    rows = compare_records(raw_records, platform_records, rules)
    summary = summarize(rows)
    logger.info("  → 比对结果: %s", summary)

    # 4.5 LLM 复核:对不一致记录给出判定(增强报告,不改变比对结果)
    if preprocessed.file_type == "text_log":
        _report("LLM 比对复核…" if comp_enabled else "LLM 比对复核已关闭,跳过")
        rows, review_meta = llm_review_comparison(
            rules, preprocessed.kept_lines, rows, enabled=comp_enabled
        )
        if review_meta.get("enabled"):
            llm_meta["comparison"] = review_meta
            logger.info("  → LLM 比对复核: %s", review_meta)

    # 5. 生成报告
    _report("生成报告…")
    logger.info("Step 5: 生成报告 → %s", output_dir)
    from .reporter import generate_all_reports

    paths = generate_all_reports(
        device_code=device_code,
        rules=rules,
        rows=rows,
        summary=summary,
        raw_records=raw_records,
        output_dir=output_dir,
        raw_files=list(raw_paths),
        platform_csvs=list(platform_paths),
        review_meta=llm_meta,
        quality_report=quality_report,
    )

    elapsed = time.time() - started
    logger.info("核验完成,耗时 %.1f 秒", elapsed)

    return {
        "device_code": device_code,
        "raw_file": str(raw_paths[0]),
        "raw_files": [str(p) for p in raw_paths],
        "platform_csv": str(platform_paths[0]),
        "platform_csvs": [str(p) for p in platform_paths],
        "output_dir": str(output_dir),
        "xlsx_path": str(paths["xlsx"]),
        "csv_path": str(paths["csv"]),
        "md_path": str(paths["md"]),
        "total_count": summary["total"],
        "inconsistent_count": summary["total"] - summary.get("match", 0),
        "summary": summary,
        "llm_meta": llm_meta,
        "quality": quality_report,
        "column_resolution": {
            "resolved": resolved_cols,
            "warnings": col_warnings,
        },
        "elapsed_seconds": elapsed,
    }


def _resolve_review_switches(
    llm_review: Optional[bool],
    independent_extraction: Optional[bool],
    comparison_review: Optional[bool],
) -> tuple[Optional[bool], Optional[bool]]:
    """把用户开关解析为两个阶段各自的 enabled 值(None=回退设备配置)。"""
    if llm_review is None:
        return None, None  # 完全回退设备配置
    if not llm_review:
        return False, False
    # 主开关开:独立抽取默认关(慢),比对复核默认开(快)
    indep = False if independent_extraction is None else bool(independent_extraction)
    comp = True if comparison_review is None else bool(comparison_review)
    return indep, comp


def _as_path_list(path: Union[Path, List[Path]]) -> List[Path]:
    """把单个 Path 或 Path 列表统一为列表。"""
    if isinstance(path, (list, tuple)):
        return [Path(p) for p in path]
    return [Path(path)]


def _preprocess_raw_files(
    paths: List[Path], rules: DeviceRules
) -> PreprocessResult:
    """预处理多个原始文件并合并为一份结果。

    - 文本日志: 逐文件过滤后合并 kept_lines(行号在合并后的列表中连续,供 LLM 上下文定位)
    - CSV/SQLite: 合并 kept_records

    注意:本函数只用于统计与「非文本」场景的合并;文本日志的抽取请走
    `_extract_text_logs_per_file`,那里按文件分别抽取,避免跨文件时间窗错绑。
    """
    merged_lines: List[str] = []
    merged_records: List[Dict[str, Any]] = []
    file_type: Optional[str] = None
    total_input = 0

    for i, p in enumerate(paths):
        pp = preprocess_raw_file(p, rules)
        file_type = file_type or pp.file_type
        total_input += pp.total_input_lines
        logger.info(
            "preprocess[%d/%d] type=%s name=%s exists=%s size=%s input=%d kept=%d",
            i + 1,
            len(paths),
            pp.file_type,
            p.name,
            p.exists(),
            p.stat().st_size if p.exists() else 0,
            pp.total_input_lines,
            pp.total_kept_lines,
        )
        if pp.file_type == "text_log":
            merged_lines.extend(pp.kept_lines)
        else:
            merged_records.extend(pp.kept_records)

    return PreprocessResult(
        source_file=paths[0],
        file_type=file_type or "text_log",
        kept_lines=merged_lines,
        kept_records=merged_records,
        total_input_lines=total_input,
        total_kept_lines=len(merged_lines) + len(merged_records),
    )


def _extract_text_logs_per_file(
    paths: List[Path], rules: DeviceRules
) -> Tuple[List[DeviceRecord], List[str], int, int]:
    """对每个文本日志独立预处理+抽取,避免跨文件时间窗错绑。

    背景:
      多天/多批次日志合并时,行时间戳只有 HH:MM:SS(无日期)。若直接把所有文件的
      kept_lines 拼成一个列表,前一天的 23:59 行与第二天的 00:00 行仅相隔 1 分钟,
      会让 SN/ICCID/MAC 跨日错绑 → 大量 1:1 冲突 + dedupe 标记 low → 报告「空白」。

    修法:
      每个文件独立走 preprocess + extract_from_text_log(同串口+时间窗只在单文件内生效),
      再把 records 合并。同时把 source_line 加上累计行偏移,映射回全局 kept_lines,
      让 LLM 复核(_grab_context / _conflict_context)用 source_line 索引时仍正确。

    Returns:
        records:           跨文件合并的 DeviceRecord 列表(source_line 已全局化)
        merged_kept_lines: 跨文件拼接的 kept_lines(全局索引,供 LLM 上下文)
        total_input:       所有文件输入总行数
        total_kept:        所有文件保留总行数
    """
    all_records: List[DeviceRecord] = []
    merged_kept_lines: List[str] = []
    total_input = 0
    total_kept = 0
    line_offset = 0  # 累计的「保留行」偏移,用于把 per-file source_line 映射到全局

    for i, path in enumerate(paths):
        pre = preprocess_raw_file(path, rules)
        logger.info(
            "extract_text[%d/%d] file=%s type=%s input=%d kept=%d",
            i + 1,
            len(paths),
            path.name,
            pre.file_type,
            pre.total_input_lines,
            pre.total_kept_lines,
        )
        if pre.file_type != "text_log":
            # SQLite/CSV 路径在主流程的 elif 分支处理;此处只处理文本日志
            total_input += pre.total_input_lines
            total_kept += pre.total_kept_lines
            continue

        recs = extract_from_text_log(pre.kept_lines, rules)
        logger.info(
            "  → 从 %s 抽取到 %d 条 DeviceRecord",
            path.name,
            len(recs),
        )

        # 调整 source_line 为全局索引,与 merged_kept_lines 对齐
        # (trace_response_time 仍保留原 HH:MM:SS;LLM 上下文查找走 source_line 索引)
        for r in recs:
            if r.source_line is not None:
                r.source_line = r.source_line + line_offset

        all_records.extend(recs)
        merged_kept_lines.extend(pre.kept_lines)
        line_offset += len(pre.kept_lines)
        total_input += pre.total_input_lines
        total_kept += pre.total_kept_lines

    return all_records, merged_kept_lines, total_input, total_kept


def _read_platform_csvs(paths: List[Path]) -> List[Dict[str, str]]:
    """读取多个平台表格文件(.xls/.xlsx/.csv)并合并记录(去重由比对阶段按追溯码主键处理)。"""
    merged: List[Dict[str, str]] = []
    for p in paths:
        merged.extend(read_spreadsheet(p))
    return merged


def _normalize_platform_records(
    raw: List[Dict[str, str]],
    rules: DeviceRules,
    resolved_cols: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """把平台 CSV 行规范化为统一字段名。

    resolved_cols 为已解析(含模糊匹配纠错)后的列名映射;为空时回退到配置原值。
    """
    cols = resolved_cols or rules.platform_csv_columns or {}
    normalized: List[Dict[str, Any]] = []
    for row in raw:
        normalized.append(
            {
                "sn": _col(row, cols.get("sn", "sn")),
                "mac": _col(row, cols.get("mac", "mac")),
                "iccid": _col(row, cols.get("iccid", "iccid")) if rules.is_4g else None,
                "trace_code": _col(row, cols.get("trace_code", "trace_code")),
                "_raw": row,
            }
        )
    return normalized


def _required_fields(rules: DeviceRules) -> List[str]:
    required = ["sn", "mac", "trace_code"]
    if rules.is_4g:
        required.append("iccid")
    return required


def _resolve_platform_columns(
    raw: List[Dict[str, str]], rules: DeviceRules
) -> Tuple[Dict[str, str], List[str]]:
    """解析平台列名映射:精确匹配优先,匹配不上时做模糊匹配自动纠错。

    返回 (resolved, warnings):
    - resolved: field → 实际存在的 CSV 列名(纠错后)
    - warnings: 每一条自动纠错 / 无法解析的说明

    若某字段既无法精确匹配也无法模糊匹配,则不会出现在 resolved 中,
    由调用方按「必填字段缺失」报错。
    """
    cols = rules.platform_csv_columns or {}
    actual_cols = _actual_csv_columns(raw)
    actual_set = {_norm_col(c) for c in actual_cols}

    resolved: Dict[str, str] = {}
    warnings: List[str] = []

    for field in _required_fields(rules):
        mapped = (cols.get(field) or "").strip()
        if not mapped:
            warnings.append(f"{field} 未配置平台列名")
            continue

        # 1) 精确匹配(忽略大小写/空白/下划线等)
        exact = next((c for c in actual_cols if _norm_col(c) == _norm_col(mapped)), None)
        if exact is not None:
            resolved[field] = exact
            continue

        # 2) 模糊匹配:语义同义词 + 文本相似度,取置信度最高者
        best_col, best_score = None, 0.0
        for cand in actual_cols:
            s = _column_similarity(field, mapped, cand)
            if s > best_score:
                best_col, best_score = cand, s

        if best_col is not None and best_score >= _FUZZY_THRESHOLD:
            resolved[field] = best_col
            warnings.append(
                f"{field}: 列名 {mapped!r} 不存在,已自动匹配到 {best_col!r}"
                f"(置信度 {best_score:.0%})"
            )
        else:
            hint = ""
            if best_col is not None:
                hint = f",最接近的是 {best_col!r}(置信度 {best_score:.0%},过低)"
            warnings.append(
                f"{field}: 列名 {mapped!r} 不存在且无法自动匹配"
                f"(CSV 实际列为: {', '.join(actual_cols)}{hint})"
            )

    # 纠错成功后写回 device.json,让配置自愈(下次无需再次模糊匹配)
    _persist_platform_columns(rules, resolved)

    return resolved, warnings


def _has_missing_required(resolved: Dict[str, str], rules: DeviceRules) -> bool:
    """判断必填字段是否仍有缺失(无法精确/模糊解析)。"""
    return any(f not in resolved for f in _required_fields(rules))


def _actual_csv_columns(raw: List[Dict[str, str]]) -> List[str]:
    """取第一行键作为真实列名(去空白、去 None)。"""
    if not raw:
        return []
    return [k.strip() for k in raw[0].keys() if k is not None]


def _persist_platform_columns(rules: DeviceRules, resolved: Dict[str, str]) -> None:
    """把纠错后的列名映射写回 device.json(尽力而为,失败不影响核验)。"""
    if not resolved:
        return
    rules_dir = getattr(rules, "rules_dir", None)
    if not rules_dir:
        return
    device_json_path = rules_dir / "device.json"
    try:
        if not device_json_path.is_file():
            return
        data = json.loads(device_json_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        cols = dict(data.get("platform_csv_columns") or {})
        changed = False
        for field, real_col in resolved.items():
            if cols.get(field) != real_col:
                cols[field] = real_col
                changed = True
        if changed:
            data["platform_csv_columns"] = cols
            device_json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            logger.info("已把纠错后的 platform_csv_columns 写回 %s", device_json_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("写回 platform_csv_columns 失败(不影响核验): %s", e)


def _validate_platform_columns(raw: List[Dict[str, str]], rules: DeviceRules) -> List[str]:
    """严格校验 platform_csv_columns 映射是否精确命中(不含模糊匹配)。

    保留用于测试与需要「严格模式」的场景。
    """
    if not raw:
        return []

    actual_cols = _actual_csv_columns(raw)
    actual_set = {_norm_col(c) for c in actual_cols}
    cols = rules.platform_csv_columns or {}

    problems: List[str] = []
    for field in _required_fields(rules):
        mapped = (cols.get(field) or "").strip()
        if not mapped:
            problems.append(f"{field} 未配置平台列名")
            continue
        if _norm_col(mapped) not in actual_set:
            problems.append(
                f"{field} → 列名 {mapped!r} 在 CSV 中不存在(CSV 实际列为: {', '.join(actual_cols)})"
            )
    return problems


def _col(row: Dict[str, str], col_name: Optional[str]) -> Optional[str]:
    """按列名取值(忽略大小写、去空白)。"""
    if not col_name:
        return None
    target = col_name.strip().lower()
    for k, v in row.items():
        if k is None:
            continue
        if k.strip().lower() == target:
            return (v or "").strip() or None
    return None