"""报告生成器:产出三份报告。

报告①: 详细对比表 (xlsx)
报告②: 重传 CSV (仅含未上报/不一致的记录)
报告③: AI 分析报告 (md)

依赖:
- openpyxl (xlsx)
- csv (Python 内置)
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .comparator import ComparisonRow
from .extractor import DeviceRecord
from .rule_loader import DeviceRules


def generate_all_reports(
    device_code: str,
    rules: DeviceRules,
    rows: List[ComparisonRow],
    summary: Dict[str, int],
    raw_records: List[DeviceRecord],
    output_dir: Path,
    raw_file: Path,
    platform_csv: Path,
    review_meta: Dict[str, Any] | None = None,
    quality_report: Dict[str, Any] | None = None,
) -> Dict[str, Path]:
    """生成三份报告,返回各自的输出路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    xlsx_path = output_dir / f"对比表_{device_code}_{timestamp}.xlsx"
    csv_path = output_dir / f"重传数据_{device_code}_{timestamp}.csv"
    md_path = output_dir / f"AI分析报告_{device_code}_{timestamp}.md"

    generate_xlsx(rows, rules, xlsx_path)
    generate_csv(rows, rules, csv_path)
    generate_md(
        rows,
        summary,
        rules,
        raw_file,
        platform_csv,
        md_path,
        review_meta=review_meta,
        quality_report=quality_report,
    )

    return {"xlsx": xlsx_path, "csv": csv_path, "md": md_path}


# === 报告 ①: 详细对比表 (xlsx) ===

def generate_xlsx(rows: List[ComparisonRow], rules: DeviceRules, output: Path) -> None:
    """生成详细对比表的 xlsx 文件。

    列结构:
    | 追溯码 | 原始SN | 平台SN | SN一致性 |
    |       | 原始MAC | 平台MAC | MAC一致性 |
    |       | 原始ICCID | 平台ICCID | ICCID一致性 | (仅 4G)
    |       | 原始追溯码 | 平台追溯码 | 追溯码一致性 |
    | 状态 | 备注 |
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ImportError(
            "生成 xlsx 需要 openpyxl,请运行 `pip install openpyxl`"
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "详细对比"

    is_4g = rules.is_4g

    # 表头
    headers = [
        "追溯码",
        "原始 SN",
        "平台 SN",
        "SN 一致性",
        "原始 MAC",
        "平台 MAC",
        "MAC 一致性",
    ]
    if is_4g:
        headers += ["原始 ICCID", "平台 ICCID", "ICCID 一致性"]
    headers += [
        "原始追溯码",
        "平台追溯码",
        "追溯码一致性",
        "整体状态",
        "备注",
    ]
    ws.append(headers)

    # 表头样式
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4472C4")
    for col_idx, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    # 数据行
    for r in rows:
        platform = r.platform_record or {}
        row_data = [
            r.trace_code,
            _display(r.raw_record.sn if r.raw_record else None),
            _display(platform.get("sn") if isinstance(platform, dict) else (platform.sn if platform else None)),
            _match_cell(r.sn_match),
            _display(r.raw_record.mac if r.raw_record else None),
            _display(platform.get("mac") if isinstance(platform, dict) else (platform.mac if platform else None)),
            _match_cell(r.mac_match),
        ]
        if is_4g:
            row_data += [
                _display(r.raw_record.iccid if r.raw_record else None),
                _display(platform.get("iccid") if isinstance(platform, dict) else (platform.iccid if platform else None)),
                _match_cell(r.iccid_match),
            ]
        row_data += [
            _display(r.raw_record.trace_code if r.raw_record else None),
            _display(platform.get("trace_code") if isinstance(platform, dict) else (platform.trace_code if platform else None)),
            _match_cell(r.trace_code_match),
            r.overall_status,
            r.notes,
        ]
        ws.append(row_data)

        # 状态着色
        status_cell = ws.cell(row=ws.max_row, column=len(headers) - 1)
        if r.overall_status == "MISMATCH":
            status_cell.fill = PatternFill("solid", fgColor="FFC7CE")
            status_cell.font = Font(color="9C0006", bold=True)
        elif r.overall_status == "RAW_ONLY":
            status_cell.fill = PatternFill("solid", fgColor="FFE699")
            status_cell.font = Font(color="9C5700", bold=True)
        elif r.overall_status == "MATCH":
            status_cell.fill = PatternFill("solid", fgColor="C6EFCE")
            status_cell.font = Font(color="006100")

    # 列宽自动调整(粗略估算)
    for col_idx, header in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        max_len = max(
            len(str(header)),
            *(len(str(ws.cell(row=r, column=col_idx).value or "")) for r in range(2, ws.max_row + 1)),
        )
        ws.column_dimensions[col_letter].width = min(max(12, max_len + 2), 50)

    wb.save(str(output))


def _display(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _match_cell(b) -> str:
    if b is True:
        return "✓"
    if b is False:
        return "✗"
    return "-"


# === 报告 ②: 重传 CSV ===

def generate_csv(rows: List[ComparisonRow], rules: DeviceRules, output: Path) -> None:
    """生成用于重传到平台的 CSV。

    仅包含 MISMATCH / RAW_ONLY 的记录(需要修复后重传)。
    包含原始真值(从 raw_record 取) + 与平台 CSV 同构的列。
    """
    # 过滤出需要重传的记录
    to_reupload = [r for r in rows if r.overall_status in ("MISMATCH", "RAW_ONLY")]

    cols = rules.platform_csv_columns

    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        # 写表头(按 platform_csv_columns 顺序)
        header = []
        for field_name in ("sn", "mac", "trace_code", "iccid"):
            col_name = cols.get(field_name)
            if col_name and (field_name != "iccid" or rules.is_4g):
                header.append(col_name)
        writer.writerow(header)

        for r in to_reupload:
            if not r.raw_record:
                continue
            row = []
            for field_name in ("sn", "mac", "trace_code", "iccid"):
                col_name = cols.get(field_name)
                if not col_name:
                    continue
                if field_name == "iccid" and not rules.is_4g:
                    continue
                value = getattr(r.raw_record, field_name, None)
                row.append(value or "")
            writer.writerow(row)


# === 报告 ③: AI 分析报告 (md) ===

def generate_md(
    rows: List[ComparisonRow],
    summary: Dict[str, int],
    rules: DeviceRules,
    raw_file: Path,
    platform_csv: Path,
    output: Path,
    review_meta: Dict[str, Any] | None = None,
    quality_report: Dict[str, Any] | None = None,
) -> None:
    """生成 AI 分析报告的 md 文件。

    采用"自然语言描述现象"的风格,不做预设错误分类。
    """
    is_4g = rules.is_4g

    lines: List[str] = []
    lines.append(f"# {rules.display_name} 板检数据核验分析报告")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 设备型号: `{rules.device_code}` ({'4G 设备' if is_4g else '非 4G 设备'})")
    lines.append(f"> 原始数据: `{raw_file.name}`")
    lines.append(f"> 平台数据: `{platform_csv.name}`")
    lines.append("")

    # 一、总体统计
    lines.append("## 一、总体统计")
    lines.append("")
    lines.append("| 项目 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| 总设备数 | {summary.get('total', 0)} |")
    lines.append(f"| 数据完全一致 | {summary.get('match', 0)} |")
    lines.append(f"| 字段不一致 | {summary.get('mismatch', 0)} |")
    lines.append(f"| 仅板检有,平台未上报 | {summary.get('raw_only', 0)} |")
    lines.append(f"| 仅平台有,板检未找到 | {summary.get('platform_only', 0)} |")
    lines.append(f"| 数据不完整 | {summary.get('incomplete', 0)} |")
    lines.append("")

    # 校验覆盖度提示:哪些字段因一方缺失而未参与比对,防止「X match」虚假绿灯
    import collections as _col
    miss_dist = _col.Counter()
    for r in rows:
        for f in getattr(r, "missing_fields", ()):
            miss_dist[f] += 1
    if miss_dist:
        parts = [f"{f}({miss_dist[f]})" for f in sorted(miss_dist)]
        lines.append("> ⚠️ **校验覆盖度提示**:以下字段因一端缺失,未参与比对(请检查配置列名或数据完整性):**" + " / ".join(parts) + "**")
        lines.append(">")
        lines.append("> 即使「数据完全一致」数量较高,也只代表**已比对字段**的一致,缺失字段的真实一致性未知。")
        lines.append("")

    # 一点五、数据质量校验(去重 / 唯一性)
    if quality_report:
        lines.append("## 一·五、数据质量校验")
        lines.append("")
        lines.append("| 项目 | 数量 |")
        lines.append("|---|---|")
        lines.append(f"| 原始抽取记录数 | {quality_report.get('raw_count', 0)} |")
        lines.append(f"| 归一去重后的记录数 | {quality_report.get('cleaned_count', 0)} |")
        lines.append(f"| 去重剔除的重复记录 | {quality_report.get('deduped', 0)} |")
        lines.append(f"| 一一对应冲突 | {quality_report.get('conflicts', 0)} |")
        lines.append(f"| ICCID 长度异常 | {quality_report.get('truncated_iccid', 0)} |")
        lines.append("")
        details = quality_report.get("details") or []
        if details:
            lines.append("> 校验明细:")
            lines.append(">")
            for d in details[:50]:
                lines.append(f"> - {d}")
            if len(details) > 50:
                lines.append(f"> - … 还有 {len(details) - 50} 条未列出")
            lines.append("")
        if quality_report.get("conflicts"):
            lines.append(
                "> ⚠️ 存在「一个字段值对应多个追溯码」的冲突,疑似字段绑定错误。"
                "已在本报告与 xlsx 中标记为低置信度,请人工复核。"
            )
            lines.append("")

    # 二、关键发现
    lines.append("## 二、关键发现")
    lines.append("")
    findings = _identify_findings(rows, rules)
    if findings:
        for i, f in enumerate(findings, 1):
            lines.append(f"{i}. {f}")
    else:
        lines.append("- 暂无明显异常。")
    lines.append("")

    # 三、详细现象描述(每条不一致)
    lines.append("## 三、详细现象描述")
    lines.append("")

    mismatch_rows = [r for r in rows if r.overall_status == "MISMATCH"]
    raw_only_rows = [r for r in rows if r.overall_status == "RAW_ONLY"]
    platform_only_rows = [r for r in rows if r.overall_status == "PLATFORM_ONLY"]

    if mismatch_rows:
        lines.append(f"### 3.1 字段不一致({len(mismatch_rows)} 条)")
        lines.append("")
        lines.append("| 追溯码 | 字段 | 原始值 | 平台值 | 现象 |")
        lines.append("|---|---|---|---|---|")
        for r in mismatch_rows[:100]:  # 最多展示 100 条
            for line_desc in _describe_mismatch(r):
                lines.append(line_desc)
        if len(mismatch_rows) > 100:
            lines.append(f"\n> 还有 {len(mismatch_rows) - 100} 条不一致记录未列出,详见 xlsx 报告。")
        lines.append("")

    if raw_only_rows:
        lines.append(f"### 3.2 仅板检记录中有,追溯平台未上传({len(raw_only_rows)} 条)")
        lines.append("")
        lines.append("| 追溯码 | 原始 SN | 原始 MAC | 原始 ICCID | 现象 |")
        lines.append("|---|---|---|---|---|")
        for r in raw_only_rows[:50]:
            raw = r.raw_record
            if not raw:
                continue
            lines.append(
                f"| {r.trace_code or '-'} | {raw.sn or '-'} | {raw.mac or '-'} "
                f"| {raw.iccid or '-'} | 板检完成测试,但平台未收到该设备的任何记录 |"
            )
        if len(raw_only_rows) > 50:
            lines.append(f"\n> 还有 {len(raw_only_rows) - 50} 条仅原始记录未列出。")
        lines.append("")

    if platform_only_rows:
        lines.append(f"### 3.3 仅平台记录中有,板检日志未找到({len(platform_only_rows)} 条)")
        lines.append("")
        lines.append("| 平台追溯码 | 平台 SN | 平台 MAC | 现象 |")
        lines.append("|---|---|---|---|---|")
        for r in platform_only_rows[:50]:
            pr = r.platform_record or {}
            lines.append(
                f"| {pr.get('trace_code') or '-'} | {pr.get('sn') or '-'} "
                f"| {pr.get('mac') or '-'} | 追溯平台显示已上传,但板检日志中未发现对应测试记录 |"
            )
        if len(platform_only_rows) > 50:
            lines.append(f"\n> 还有 {len(platform_only_rows) - 50} 条仅平台记录未列出。")
        lines.append("")

    # 四、原因分析与修复建议
    lines.append("## 四、原因分析与修复建议")
    lines.append("")
    suggestions = _suggest_remediation(rows, rules)
    for s in suggestions:
        lines.append(f"- {s}")
    lines.append("")

    # 四点五、LLM 复核结论(若启用)
    if review_meta:
        section = _render_llm_review_section(review_meta)
        if section:
            lines.extend(section)
            lines.append("")

    # 五、附录
    lines.append("## 五、附录")
    lines.append("")
    lines.append('- 本报告由"板检数据核验 Agent"自动生成')
    lines.append(f"- 设备规则版本: {rules.version}")
    lines.append("- 详细对比数据见同目录下 xlsx 文件")
    lines.append("- 可直接重传的数据见同目录下 csv 文件")

    output.write_text("\n".join(lines), encoding="utf-8")


def _identify_findings(rows: List[ComparisonRow], rules: DeviceRules) -> List[str]:
    """从比对结果中识别关键现象(用自然语言描述)。"""
    findings: List[str] = []

    mismatches = [r for r in rows if r.overall_status == "MISMATCH"]
    raw_only = [r for r in rows if r.overall_status == "RAW_ONLY"]
    platform_only = [r for r in rows if r.overall_status == "PLATFORM_ONLY"]
    # 双方都存在、可逐字段比对的记录(比对基准,不含平台/日志孤岛)
    matched = [r for r in rows if r.raw_record is not None and r.platform_record is not None]

    # SN 全错位现象(已知 A02FG bug):在可比对的记录中占比足够高才判定
    sn_mismatch = [r for r in mismatches if r.sn_match is False]
    if matched and len(sn_mismatch) >= len(matched) * 0.8:
        findings.append(
            f"在 {len(sn_mismatch)} 条记录中,平台上的 SN 与日志中的真实 SN 完全不一致。"
            f"这与 {rules.display_name} 板检软件将内部 UUID 错误地填入 SN 字段的已知 Bug 特征高度吻合,"
            f"建议立即从日志中提取真实 SN 并修复平台数据。"
        )

    # 全部 MAC 一致 + SN 错位
    mac_only_ok = [r for r in mismatches if r.sn_match is False and r.mac_match is True]
    if matched and len(mac_only_ok) >= len(matched) * 0.5:
        findings.append(
            f"{len(mac_only_ok)} 条记录中,MAC 与追溯码完全一致,但 SN 全部错位,"
            f"进一步证实'设备身份信息(MAC/ICCID/追溯码)正确,SN 字段被错误填充'这一根因。"
        )

    if raw_only:
        findings.append(
            f"{len(raw_only)} 台设备的板检测试已完成,但追溯平台完全未收到这些设备的数据,"
            f"建议检查板检上位机的上报链路是否正常。"
        )

    if platform_only:
        findings.append(
            f"{len(platform_only)} 条平台记录无法在板检日志中找到对应的测试,"
            f"可能是日志丢失、上报时间错位或手动补录造成的孤岛数据。"
        )

    if rules.is_4g:
        iccid_mismatch = [r for r in mismatches if r.iccid_match is False]
        if iccid_mismatch:
            findings.append(
                f"{len(iccid_mismatch)} 条 4G 设备的 ICCID 在两端不一致,"
                f"请检查 ICCID 是否被截断或被错误映射到其他设备。"
            )

    if not findings:
        findings.append("所有设备的 4 个核心字段(SN/MAC/ICCID/追溯码)均一致,数据核验通过。")

    return findings


def _describe_mismatch(r: ComparisonRow) -> List[str]:
    """生成单条不一致的自然语言描述。"""
    descriptions = []
    raw = r.raw_record
    pr = r.platform_record or {}

    if raw is None:
        return [f"| {r.trace_code or '-'} | - | - | - | 仅平台有记录 |"]

    if r.sn_match is False:
        descriptions.append(
            f"| {r.trace_code or '-'} | SN | {raw.sn or '-'} | {pr.get('sn') or '-'} | "
            f"SN 在两端不同 |"
        )
    if r.mac_match is False:
        descriptions.append(
            f"| {r.trace_code or '-'} | MAC | {raw.mac or '-'} | {pr.get('mac') or '-'} | "
            f"MAC 在两端不同 |"
        )
    if r.iccid_match is False:
        descriptions.append(
            f"| {r.trace_code or '-'} | ICCID | {raw.iccid or '-'} | {pr.get('iccid') or '-'} | "
            f"ICCID 在两端不同 |"
        )
    if r.trace_code_match is False:
        descriptions.append(
            f"| {r.trace_code or '-'} | 追溯码 | {raw.trace_code or '-'} | {pr.get('trace_code') or '-'} | "
            f"追溯码在两端不同 |"
        )
    return descriptions or [f"| {r.trace_code or '-'} | - | - | - | {r.notes or '字段不一致'} |"]


def _suggest_remediation(rows: List[ComparisonRow], rules: DeviceRules) -> List[str]:
    """根据比对结果给出修复建议。"""
    suggestions: List[str] = []

    mismatches = [r for r in rows if r.overall_status == "MISMATCH"]
    if mismatches:
        # 主要看 SN 错位
        sn_mismatch = sum(1 for r in mismatches if r.sn_match is False)
        if sn_mismatch:
            suggestions.append(
                f"**修复 SN 错位**:将平台上 {sn_mismatch} 条记录的 SN 字段替换为日志中提取的真实 SN"
                f"(详见同目录下 xlsx 文件 '原始 SN' 列与 '重传 CSV')。"
            )
        # MAC 错位
        mac_mismatch = sum(1 for r in mismatches if r.mac_match is False)
        if mac_mismatch:
            suggestions.append(
                f"**修复 MAC 错位**:{mac_mismatch} 条记录的 MAC 在两端不同,"
                f"请检查板检上位机写入 MAC 时是否被错误映射。"
            )
        # ICCID 错位(仅 4G)
        if rules.is_4g:
            iccid_mismatch = sum(1 for r in mismatches if r.iccid_match is False)
            if iccid_mismatch:
                suggestions.append(
                    f"**修复 ICCID 错位**:{iccid_mismatch} 条 4G 设备的 ICCID 错位,"
                    f"建议从日志原始数据中重写平台 ICCID 字段。"
                )

    # 仅原始 → 上报链路问题
    raw_only = [r for r in rows if r.overall_status == "RAW_ONLY"]
    if raw_only:
        suggestions.append(
            f"**修复未上报问题**:{len(raw_only)} 台设备已完成板检但平台未收到,"
            f"请检查板检上位机的网络/上报服务,必要时手动用重传 CSV 补录。"
        )

    # 仅平台 → 日志缺失
    platform_only = [r for r in rows if r.overall_status == "PLATFORM_ONLY"]
    if platform_only:
        suggestions.append(
            f"**核查孤岛数据**:{len(platform_only)} 条平台记录无对应日志,"
            f"建议核对板检日期与日志日期是否一致,排查日志归档丢失。"
        )

    # 根治建议
    if any(r.sn_match is False for r in mismatches):
        suggestions.append(
            "**根治(软件 Bug)**:建议修改板检上位机,使其在写入数据库时使用"
            "'本条测试循环下发的真实 SN'(从命令参数中提取),而非使用软件自增/随机的 UUID。"
        )

    if not suggestions:
        suggestions.append("数据一致性良好,无需修复。")

    return suggestions


def _render_llm_review_section(review_meta: Dict[str, Any]) -> List[str]:
    """把 LLM 复核的元信息渲染成报告段落。"""
    lines: List[str] = []
    lines.append("## 四·五、LLM 复核(交叉验证)结论")
    lines.append("")
    lines.append("> 本报告由确定性抽取管线生成后,又经大模型作为独立第二视角交叉验证。")

    extract = review_meta.get("extraction") or {}
    if extract.get("enabled"):
        if extract.get("skipped"):
            lines.append(f"- 独立抽取:跳过({extract.get('skipped')})。")
        elif not extract.get("extraction_effective"):
            lines.append(
                f"- 独立抽取:**未生效**。共 {extract.get('calls', 0)} 个分块,大模型成功解析 0 块"
                f"(抽取 {extract.get('llm_extracted', 0)} 条)。模型输出不可解析/被截断,"
                f"交叉验证实际未进行,结果仅来自确定性管线。建议检查代理/模型,或关闭该复核。"
            )
        else:
            lines.append(
                f"- 独立抽取:大模型分块抽取 {extract.get('llm_extracted', 0)} 条,"
                f"与正则对账发现 {extract.get('disagreements', 0)} 处分歧,"
                f"其中 {extract.get('corrected', 0)} 处经硬性约束校验后修正。"
            )

    compare = review_meta.get("comparison") or {}
    if compare.get("enabled"):
        if compare.get("skipped"):
            lines.append(f"- 比对复核:跳过({compare.get('skipped')})。")
        elif not compare.get("review_effective"):
            lines.append(
                f"- 比对复核:**未生效**。存在不一致记录但大模型未返回任何有效判定"
                f"(复核 {compare.get('reviewed', 0)} 条)。建议检查代理/模型。"
            )
        else:
            verdicts = compare.get("verdicts") or {}
            lines.append(
                f"- 比对复核:复核 {compare.get('reviewed', 0)} 条不一致记录,"
                f"判定为 真实问题 {verdicts.get('confirmed', 0)} 条、"
                f"疑似绑定错误 {verdicts.get('binding_artifact', 0)} 条、"
                f"疑似批次不同 {verdicts.get('data_scope', 0)} 条、"
                f"疑似误报 {verdicts.get('false_positive', 0)} 条。"
            )
            lines.append("  - 各条目的复核结论已写入 xlsx『备注』列,供人工最终确认。")

    return lines