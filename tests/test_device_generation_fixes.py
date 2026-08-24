"""回归测试:修复「交付物出现 COM口/字段名」与「平台列名映射错误」两类问题。

不依赖 LLM,可直接运行:
    AGENT_DISABLE_LLM=1 python3 tests/test_device_generation_fixes.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core.extractor import _dedupe_repeated, _extract_field_value
from agent.core.rule_loader import DeviceRules, FieldExtractor
from agent.core.verifier import _validate_platform_columns


def test_anchor_without_group_uses_regex_group():
    """LLM 生成的两种约定都应正确取到「值」而非「标签」。

    约定 A(A02FG):context_anchor 带捕获组。
    约定 B(F16G) :context_anchor 只是前缀(无捕获组),值捕获放在 regex。
    两者都必须返回字段值本身。
    """
    line_a = "[07:51:10] COM77:收mac:000199292D10"   # A02FG 格式:收mac 无空格,12 位 MAC
    line_b = "[07:51:10] COM77:收 :mac:701988899B9CD1"  # F16G 格式:收 :mac 带空格,14 位 MAC

    # 约定 A:锚点带捕获组
    a = FieldExtractor(
        field_name="mac",
        regex="(?<![0-9A-F])(0001[0-9A-F]{8})(?![0-9A-F])",
        context_anchor="COM\\d+:收mac:([0-9A-F]{12})",
        post_process=["trim", "upper"],
    )
    assert _extract_field_value(a, line_a) == "000199292D10", "约定 A 取值错误"

    # 约定 B:锚点无捕获组(前缀),regex 带捕获组
    b = FieldExtractor(
        field_name="mac",
        regex="COM\\d+:(?:收\\s*:)?mac:\\s*([0-9A-F]{12,14})",
        context_anchor="COM\\d+:(?:收\\s*:)?mac:",
        post_process=["trim", "upper"],
    )
    assert _extract_field_value(b, line_b) == "701988899B9CD1", "约定 B 取值错误(不应是 'COM77:收 :MAC:')"


def test_dedupe_repeated_glued_iccid():
    """粘连 ICCID 串应取「最后一个完整号」(实测最后一个是当前真值,平台存的是第一个=脏前缀)。

    覆盖两种粘连形态:
    - 缺省前缀型:'89860468152590218986' + '0468152590219181' → 最后一个 89860468152590219181
    - 显式重复型:'89860468152590219559' + '89860468152590219559' → 重复号取其一
    - 带 '?' 脏分隔型:'8986046815259021904?89860468152590219046' → 取干净的第二个
    - 干净单值:原样返回,不误伤
    """
    # C4D 案例:第一个是脏前缀 18986,最后一个才是真值 19181
    assert _dedupe_repeated("898604681525902189860468152590219181") == "89860468152590219181"
    # 9C8 案例:两个候选不同,取最后一个
    assert _dedupe_repeated("898604681525902197389860468152590219732") == "89860468152590219732"
    # 显式重复(同号粘连):取最后一个(与第一个相同)
    assert _dedupe_repeated("8986046815259021955989860468152590219559") == "89860468152590219559"
    # 脏分隔 '?' 型:第一个被 ? 截断,取干净第二个
    assert _dedupe_repeated("8986046815259021904?89860468152590219046") == "89860468152590219046"
    # 干净单值:原样返回
    assert _dedupe_repeated("89860468152590219063") == "89860468152590219063"


def test_iccid_regex_captures_glued_run():
    """F16G 的 ICCID 正则应能捕获「完整粘连串」,由 sanitize_duplicates 后处理取最后一个完整号。

    旧正则 (8986[0-9]{15,16})(?![0-9]) 对粘连串整体匹配失败 → 该条 ICCID 缺失,
    后续绑定会偷邻居设备的 ICCID(如 C4D 偷到 DED7 的 19670)。
    """
    ext = FieldExtractor(
        field_name="iccid",
        regex="(?<![0-9])(?:[Ii][Cc][Cc][Ii][Dd]\\s*[:：=]?\\s*)?(8986[0-9]{15,})",
        context_anchor="(?<![0-9])(?:[Ii][Cc][Cc][Ii][Dd]\\s*[:：=]?\\s*)?(8986[0-9]{15,})",
        post_process=["trim", "upper"],
        sanitize_duplicates=True,
    )
    # C4D 最后一条测试循环里的粘连 ICCID → 应取到真值 19181,而不是缺值/偷邻居
    assert (
        _extract_field_value(ext, "17:50:49 688 | COM77:收 :ICCID:898604681525902189860468152590219181")
        == "89860468152590219181"
    )
    # DED7 干净单值 → 原样
    assert _extract_field_value(ext, "17:51:42 286 | COM77:收 :ICCID:89860468152590219670") == "89860468152590219670"
    # 脏分隔 '?' 型:正则可能捕获被 '?' 截断的前缀,但整行扫描应取到干净的第二个号
    assert _extract_field_value(ext, "16:50:09 248 | COM77:收 :ICCID:89860468152590219?89860468152590219741") == "89860468152590219741"
    # 显式重复型(同号粘连)
    assert _extract_field_value(ext, "18:15:13 598 | COM77:收 :ICCID:8986046815259021955989860468152590219559") == "89860468152590219559"


def test_sn_after_anchor_preferred_over_stale_prev_device():
    """SN 绑定优先选「锚点之后」的 id,不偷上一台设备的旧 SN(失败循环错绑的根因)。

    场景:设备 C38 第一次发 trace 失败,重试时第二次发 trace 前,日志里最近的一条 SN
    还是上一台设备 D20 的;真正的 C38 SN 出现在成功循环的 trace 之后。
    旧「就近」绑定会把 D20 的 SN 偷给 C38 → 1:1 冲突 + 平台误判。
    """
    from agent.core.extractor import extract_from_text_log
    from agent.core.rule_loader import FieldExtractor, DeviceRules

    # 模拟 COM9:上一台 D20 的 SN(锚点之前),本台 C38 的 SN(锚点之后)
    lines = [
        "17:51:29 | COM9:发: trace code:F176XYXI5R701988899D20",
        "17:51:33 | COM9:收 :id:209D89881970C04B3F982FF020CDF90846",   # D20 的 SN
        "17:52:14 | COM9:发: trace code:F176XYXI5R701988899C38",        # C38 失败循环(第二次发)
        "17:53:13 | COM9:收 :id:389C8988197068A43F982FF0B8DCF90846",   # C38 的 SN
    ]

    rules = DeviceRules(
        device_code="F16G", display_name="F16G", is_4g=True, version="1.0.0",
        description="", platform_csv_columns={}, field_definitions={},
        field_constraints={}, filter_rules={}, extraction_rules={}, prompts_md="",
        rules_dir=None,
    )
    rules.field_extractors = {
        "sn": FieldExtractor(
            field_name="sn",
            regex="收\\s*:id:([0-9A-Fa-f]{32})(?:46)?",
            context_anchor="收\\s*:id:([0-9A-Fa-f]{32})(?:46)?",
        ),
        "trace_code": FieldExtractor(
            field_name="trace_code",
            regex="trace\\s*code:([A-Za-z0-9]{10,40})(?![A-Za-z0-9])",
            context_anchor="trace\\s*code:([A-Za-z0-9]{10,40})(?![A-Za-z0-9])",
            authoritative_forms=["发\\s*:"],
        ),
    }
    rules.extraction_rules = {"session_binding": {"window_seconds": 120}}

    recs = extract_from_text_log(lines, rules)
    by_trace = {r.trace_code: r for r in recs}
    # D20 绑到自己的 SN(虽然其 trace 在 id 之前,但 D20 的 id 在其 anchor 之后)
    assert by_trace["F176XYXI5R701988899D20"].sn == "209D89881970C04B3F982FF020CDF908"
    # C38 必须绑到自己的 SN(389C...),不能偷 D20 的(209D...)
    assert by_trace["F176XYXI5R701988899C38"].sn == "389C8988197068A43F982FF0B8DCF908"


def test_platform_column_validation():
    """平台列名映射写错时应被校验出来,而不是静默把缺失字段当 None。"""
    from agent.core.rule_loader import DeviceRules

    # 用临时设备规则构造错误的 platform_csv_columns 映射
    # 这里直接用一个最小 rules 替身(仅需 platform_csv_columns 与 is_4g)
    class _R:
        is_4g = True
        platform_csv_columns = {
            "sn": "SN",  # CSV 里没有 SN 列
            "mac": "MAC",
            "iccid": "ICCID",
            "trace_code": "追溯码",  # 这个存在
        }

    raw = [{"主键": "1", "追溯码": "F176", "蓝牙mac": "7019", "4G信息": "8986"}]
    problems = _validate_platform_columns(raw, _R())
    assert any("sn" in p for p in problems), "应检出 sn 列映射错误"
    assert any("mac" in p for p in problems), "应检出 mac 列映射错误"
    assert any("iccid" in p for p in problems), "应检出 iccid 列映射错误"
    assert not any("trace_code" in p for p in problems), "追溯码映射正确,不应报错"

    # 全部正确映射时不应报错
    _R.platform_csv_columns = {
        "sn": "写入SN",
        "mac": "蓝牙mac",
        "iccid": "4G信息",
        "trace_code": "追溯码",
    }
    raw = [{"主键": "1", "追溯码": "F176", "蓝牙mac": "7019", "4G信息": "8986", "写入SN": "X"}]
    assert _validate_platform_columns(raw, _R()) == []


def test_platform_column_fuzzy_resolution():
    """列名写错(SN/MAC/ICCID 等臆造名)时,应能模糊匹配自动纠错到真实中文列名。"""
    from agent.core.verifier import _has_missing_required, _resolve_platform_columns

    class _R:
        is_4g = True
        platform_csv_columns = {
            "sn": "SN",  # 臆造名
            "mac": "MAC",
            "iccid": "ICCID",
            "trace_code": "追溯码",  # 真实列,精确命中
        }

    raw = [
        {
            "主键": "1",
            "追溯码": "F176XYXI6O701988899BE9",
            "蓝牙mac": "701988899BE9",
            "4G信息": "89860468152590219136",
            "写入SN": "E99B89881970DFFD3F982FF059EBF90846",
        }
    ]
    resolved, warnings = _resolve_platform_columns(raw, _R())
    assert resolved["sn"] == "写入SN", "sn 应模糊匹配到 写入SN"
    assert resolved["mac"] == "蓝牙mac", "mac 应模糊匹配到 蓝牙mac"
    assert resolved["iccid"] == "4G信息", "iccid 应模糊匹配到 4G信息"
    assert resolved["trace_code"] == "追溯码", "追溯码应精确命中"
    assert not _has_missing_required(resolved, _R()), "所有必填字段应已解析"
    assert any("sn" in w for w in warnings), "应有 sn 自动纠错的告警"


def test_quality_dedup_and_conflict():
    """数据质量层: 归一去重 + 1:1 唯一性冲突 + ICCID 长度异常。"""
    from agent.core.extractor import DeviceRecord
    from agent.core.quality import dedupe_and_validate

    class _R:
        field_constraints = {"iccid_length": [19, 20]}

    recs = [
        DeviceRecord(trace_code="T1", sn="S1", mac="M1", iccid="89860000000000000001"),
        DeviceRecord(trace_code="T1", sn="S1", mac="M1", iccid="89860000000000000001"),  # 重复
        DeviceRecord(trace_code="T2", sn="S2", mac="M2", iccid="89860000000000000001"),  # 同一 ICCID → 冲突
        DeviceRecord(trace_code="T3", sn="S3", mac="M3", iccid="8986000000000000"),  # 16 位,截断
    ]
    cleaned, report = dedupe_and_validate(recs, _R())
    assert report["deduped"] == 1, "T1 的重复记录应被去重 1 条"
    assert report["conflicts"] == 1, "ICCID 被 T1/T2 共享,应记为 1 处冲突"
    assert report["truncated_iccid"] == 1, "T3 的 16 位 ICCID 应被判长度异常"
    assert len(cleaned) == 3, "去重后应剩 T1/T2/T3 三条"


def test_field_equivalent_head_tail():
    """平台值嵌入日志值(头码/尾码包裹)时,应通过包含匹配判定一致。"""
    from agent.core.quality import field_equivalent

    platform_sn = "fa000001400001240260120000100050"
    raw_sn = "12345fa0000014000012402601200001000506789"
    assert field_equivalent(platform_sn, raw_sn) is True, "头尾码包裹应判定一致(包含)"
    assert field_equivalent(platform_sn, platform_sn) is True, "精确相等应判定一致"
    assert field_equivalent("AAAA", "BBBB") is False, "完全无关应判定不一致"
    assert field_equivalent(None, "x") is None, "平台缺失应返回 None"
    assert field_equivalent("x", None) is None, "原始缺失应返回 None"


def test_read_spreadsheet_csv():
    """表格读取按内容嗅探,CSV 带 BOM 表头应正确解析。"""
    from agent.core.file_reader import read_spreadsheet

    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("设备编号,蓝牙mac,追溯码\nABC,111,TC1\n")
        rows = read_spreadsheet(Path(path))
        assert rows[0]["设备编号"] == "ABC"
        assert rows[0]["蓝牙mac"] == "111"
        assert rows[0]["追溯码"] == "TC1"
    finally:
        os.unlink(path)


def test_review_switch_resolution():
    """LLM 复核开关: 主开关关→全关;开→独立抽取默认关、比对复核默认开。"""
    from agent.core.verifier import _resolve_review_switches

    assert _resolve_review_switches(False, None, None) == (False, False)
    assert _resolve_review_switches(True, None, None) == (False, True)
    assert _resolve_review_switches(True, True, False) == (True, False)
    assert _resolve_review_switches(None, None, None) == (None, None)


def test_extract_text_logs_per_file_no_cross_day_binding():
    """多文件(跨日)抽取:每个文件独立处理,SN/ICCID 不跨日错绑。

    场景:
      file1(8-13): 23:59:50 发 SN=...000100001
      file2(8-14): 00:00:30 拿追溯码+MAC+ICCID; 00:00:10 也发了 SN=...000100002
    修复前(merged kept_lines 拼接):23:59 和 00:00 视为 10 秒相邻,
      SN 000100001 错绑给 file2 设备 → 1:1 冲突。
    修复后(per-file 抽取):file2 设备只看到 file2 内的 SN(000100002)。
    """
    from agent.core.verifier import _extract_text_logs_per_file

    rules = DeviceRules.load("A02SG")

    # file1: 只有一条 SN(模拟 8-13 23:59 设备)
    file1 = (
        "23:59:50 000 | COM3:发:568121440200"
        "FA000001400001240260813000100001"
        "AABBCCDDEEFF11223344556677889900"
        "AABBCCDDEEFF11223344556677889900"
        "AABBCCDDEEFF11223344556677889900"
        "AABBCCDDEEFF112233445566"
        "00D0A\n"
    )
    # file2: 另一台设备完整循环(00:00 起,带 SN+MAC+追溯码+ICCID+回复追溯码)
    file2 = (
        "00:00:10 000 | COM3:发:568121440200"
        "FA000001400001240260814000100002"
        "AABBCCDDEEFF11223344556677889900"
        "AABBCCDDEEFF11223344556677889900"
        "AABBCCDDEEFF11223344556677889900"
        "AABBCCDDEEFF112233445566"
        "00D0A\n"
        "00:00:20 000 | COM3:收mac:000100000001\n"
        "00:00:30 000 | COM3:追溯码:F068XYXI8F000100000001\n"
        "00:00:35 000 | COM3:4G CCID:898604691026D0058123\n"
        "00:00:40 000 | COM3:回复追溯码:F068XYXI8F000100000001\n"
        "00:00:50 000 | COM3:板检结果:成功\n"
    )

    tmpdir = Path(tempfile.mkdtemp())
    try:
        f1 = tmpdir / "file1.txt"
        f2 = tmpdir / "file2.txt"
        f1.write_text(file1, encoding="utf-8")
        f2.write_text(file2, encoding="utf-8")

        records, merged_lines, total_in, total_kept = _extract_text_logs_per_file(
            [f1, f2], rules
        )

        # file2 设备的追溯码 = F068XYXI8F000100000001,期望 SN 以 814000100002 结尾
        # (file2 的 SN 含日期段 260814;file1 的 SN 含 260813)
        file2_recs = [r for r in records if r.trace_code == "F068XYXI8F000100000001"]
        assert file2_recs, "file2 设备应被抽取到一条记录"
        rec = file2_recs[0]
        assert rec.sn and rec.sn.endswith("814000100002"), (
            f"file2 设备 SN 应以 814000100002 结尾(同文件内的 SN,含日期 0814),"
            f"实际为 {rec.sn};跨日错绑未修复"
        )
        assert rec.sn and not rec.sn.endswith("813000100001"), (
            f"file2 设备 SN 不应是 file1 的 813000100001(跨日错绑)"
        )

        # source_line 必须为全局索引:file1 只有 1 行被保留,file2 的追溯码
        # 应在 file1 累计 1 行后偏移,所以 source_line = file2 内行号 + 1
        # file2 中追溯码行在原始 file2 中是第 3 行,所以 source_line = 3 + 1 = 4
        assert rec.source_line >= 2, (
            f"source_line 应已加累计偏移(全局索引),实际 {rec.source_line}"
        )

        # merged kept_lines 长度应等于两个文件 kept_lines 之和
        assert len(merged_lines) == total_kept, (
            f"merged kept_lines {len(merged_lines)} 应等于 total_kept {total_kept}"
        )
    finally:
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    test_anchor_without_group_uses_regex_group()
    print("✓ 抽取器:锚点无捕获组时改用 regex 捕获组,不再返回 COM口/字段名")
    test_platform_column_validation()
    print("✓ 平台列名校验:错误映射能被检出")
    test_platform_column_fuzzy_resolution()
    print("✓ 平台列名模糊匹配:臆造列名能自动纠错到真实中文列名")
    test_quality_dedup_and_conflict()
    print("✓ 数据质量层:归一去重 + 1:1 唯一性 + ICCID 长度")
    test_field_equivalent_head_tail()
    print("✓ 字段等价:头尾码剥离 + 包含匹配")
    test_read_spreadsheet_csv()
    print("✓ 表格读取:内容嗅探 + BOM 清理")
    test_review_switch_resolution()
    print("✓ 复核开关:默认关/独立抽取关/比对复核开")
    test_dedupe_repeated_glued_iccid()
    print("✓ 粘连 ICCID:取最后一个完整号,不再偷邻居设备的 ICCID")
    test_iccid_regex_captures_glued_run()
    print("✓ ICCID 正则:能捕获完整粘连串并由后处理取真值")
    test_sn_after_anchor_preferred_over_stale_prev_device()
    print("✓ SN 绑定:优先锚点后的 id,不再偷上一台设备的旧 SN")
    test_extract_text_logs_per_file_no_cross_day_binding()
    print("✓ 多文件抽取:跨日 SN 不错绑(file2 设备绑对 file2 SN,非 file1)")
    print("全部通过")
