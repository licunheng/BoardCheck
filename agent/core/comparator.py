"""比对器:把抽取出的原始记录与平台 CSV 逐字段对比。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .extractor import DeviceRecord
from .quality import field_equivalent
from .rule_loader import DeviceRules


def _strip_platform_suffix(platform_val: Optional[str], rules: DeviceRules) -> Optional[str]:
    """按设备配置剥离平台侧尾码/头码(平台为真值,但可能带尾码如 '46')。"""
    if not platform_val:
        return platform_val
    fc = rules.field_constraints or {}
    s = platform_val.strip()
    suffix = (fc.get("sn_strip_suffix") or "").strip()
    if suffix and s.endswith(suffix):
        s = s[: -len(suffix)]
    prefix = (fc.get("sn_strip_prefix") or "").strip()
    if prefix and s.startswith(prefix):
        s = s[len(prefix):]
    return s or None


@dataclass
class ComparisonRow:
    """单条设备的比对结果"""

    trace_code: Optional[str]  # 主键
    raw_record: Optional[DeviceRecord] = None
    platform_record: Optional[Dict[str, Any]] = None

    # 字段级一致性 (True=一致, False=不一致, None=某一方缺失)
    sn_match: Optional[bool] = None
    mac_match: Optional[bool] = None
    iccid_match: Optional[bool] = None
    trace_code_match: Optional[bool] = None

    # 状态
    overall_status: str = "UNKNOWN"  # MATCH / MISMATCH / RAW_ONLY / PLATFORM_ONLY / INCOMPLETE

    notes: str = ""
    # 校验覆盖度:哪些字段因一方缺失而未参与比对(防止虚假绿灯)
    missing_fields: tuple = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_code": self.trace_code,
            "raw_sn": self.raw_record.sn if self.raw_record else None,
            "platform_sn": self.platform_record.get("sn") if self.platform_record else None,
            "raw_mac": self.raw_record.mac if self.raw_record else None,
            "platform_mac": self.platform_record.mac if self.platform_record else None,
            "raw_iccid": self.raw_record.iccid if self.raw_record else None,
            "platform_iccid": self.platform_record.iccid if self.platform_record else None,
            "raw_trace_code": self.raw_record.trace_code if self.raw_record else None,
            "platform_trace_code": self.platform_record.trace_code if self.platform_record else None,
            "sn_match": _yn(self.sn_match),
            "mac_match": _yn(self.mac_match),
            "iccid_match": _yn(self.iccid_match),
            "trace_code_match": _yn(self.trace_code_match),
            "overall_status": self.overall_status,
            "notes": self.notes,
        }


def _yn(b: Optional[bool]) -> str:
    if b is True:
        return "✓"
    if b is False:
        return "✗"
    return "-"


def compare_records(
    raw_records: List[DeviceRecord],
    platform_records: List[Dict[str, Any]],
    rules: DeviceRules,
) -> List[ComparisonRow]:
    """比对原始记录与平台记录。

    主键策略:
    - 优先用 trace_code 匹配(最稳定)
    - 若 raw 用 mac 兜底
    """
    is_4g = rules.is_4g

    # 建立平台记录的索引
    platform_by_trace: Dict[str, Dict[str, Any]] = {}
    platform_by_mac: Dict[str, Dict[str, Any]] = {}
    for pr in platform_records:
        tc = (pr.get("trace_code") or "").upper()
        mac = (pr.get("mac") or "").upper()
        if tc:
            platform_by_trace[tc] = pr
        if mac:
            platform_by_mac[mac] = pr

    # 第一遍: 用 trace_code 匹配(主键),匹配不上再用 MAC 兜底
    matched_platform_keys: set = set()
    rows: List[ComparisonRow] = []

    for rr in raw_records:
        if not rr.trace_code:
            continue
        tc = rr.trace_code.upper()
        pr = platform_by_trace.get(tc)
        if pr is None and rr.mac:
            pr = platform_by_mac.get(rr.mac.upper())
        if pr:
            row = _build_row(rr, pr, rules, is_4g)
            rows.append(row)
            matched_platform_keys.add(id(pr))
        else:
            # 原始有,但平台没有 → RAW_ONLY
            row = ComparisonRow(
                trace_code=tc,
                raw_record=rr,
                platform_record=None,
                overall_status="RAW_ONLY",
                notes="板检记录中存在,但追溯平台未上传",
            )
            rows.append(row)

    # 第二遍: 把平台独有(未匹配的)记录加上
    for pr in platform_records:
        if id(pr) in matched_platform_keys:
            continue
        tc = pr.get("trace_code", "").upper() if pr.get("trace_code") else None
        row = ComparisonRow(
            trace_code=tc,
            raw_record=None,
            platform_record=pr,
            overall_status="PLATFORM_ONLY",
            notes="追溯平台有记录,但板检记录中未找到(可能漏检或日志丢失)",
        )
        rows.append(row)

    # 排序: 不一致 → 仅原始 → 仅平台 → 一致
    rows.sort(
        key=lambda r: (
            _status_rank(r.overall_status),
            r.trace_code or "",
        )
    )

    return rows


def _build_row(
    rr: DeviceRecord,
    pr: Dict[str, Any],
    rules: DeviceRules,
    is_4g: bool,
) -> ComparisonRow:
    """构造一条比对结果(原始+平台都存在)。

    字段匹配采用「精确相等 → 平台值嵌入日志值(头/尾码包裹)」两级判断;
    不一致时把两侧值都写进备注,交由人工裁决,不自动判定谁对谁错。
    任一方为 None 时记为「missing」而不是相等——配合报告「校验覆盖度」防止虚假绿灯。
    """
    plat_sn = _strip_platform_suffix(pr.get("sn"), rules)
    raw_sn = rr.sn
    sn_match = field_equivalent(plat_sn, raw_sn)
    if plat_sn is None or raw_sn is None:
        sn_match = None  # missing

    plat_mac = pr.get("mac")
    raw_mac = rr.mac
    mac_match = field_equivalent(plat_mac, raw_mac)
    if plat_mac is None or raw_mac is None:
        mac_match = None

    if is_4g:
        plat_iccid = pr.get("iccid")
        raw_iccid = rr.iccid
        iccid_match = field_equivalent(plat_iccid, raw_iccid)
        if plat_iccid is None or raw_iccid is None:
            iccid_match = None
    else:
        iccid_match = None

    trace_match = field_equivalent(pr.get("trace_code"), rr.trace_code)

    mismatches = []
    missing = []
    if sn_match is False:
        mismatches.append(f"SN 不一致(平台={pr.get('sn')}, 原始={rr.sn})")
    if sn_match is None and (plat_sn is None or raw_sn is None):
        missing.append("SN")
    if mac_match is False:
        mismatches.append(f"MAC 不一致(平台={pr.get('mac')}, 原始={rr.mac})")
    if mac_match is None and (plat_mac is None or raw_mac is None):
        missing.append("MAC")
    if is_4g and iccid_match is False:
        mismatches.append(f"ICCID 不一致(平台={pr.get('iccid')}, 原始={rr.iccid})")
    if is_4g and iccid_match is None and (plat_iccid is None or raw_iccid is None):
        missing.append("ICCID")
    if trace_match is False:
        mismatches.append(f"追溯码不一致(平台={pr.get('trace_code')}, 原始={rr.trace_code})")

    if mismatches:
        status = "MISMATCH"
        notes = "; ".join(mismatches)
    else:
        status = "MATCH"
        notes = ""

    return ComparisonRow(
        trace_code=rr.trace_code,
        raw_record=rr,
        platform_record=pr,
        sn_match=sn_match,
        mac_match=mac_match,
        iccid_match=iccid_match,
        trace_code_match=trace_match,
        overall_status=status,
        notes=notes,
        missing_fields=tuple(missing),
    )


def _build_row_to_dict_extra(row) -> Dict[str, Any]:
    """把 missing 字段输出到 to_dict(供报告/前端展示)。"""
    return {"missing_fields": list(getattr(row, "missing_fields", ()))}


def _status_rank(status: str) -> int:
    return {
        "MISMATCH": 0,
        "RAW_ONLY": 1,
        "PLATFORM_ONLY": 2,
        "INCOMPLETE": 3,
        "MATCH": 4,
    }.get(status, 5)


def summarize(rows: List[ComparisonRow]) -> Dict[str, int]:
    """汇总比对统计。"""
    summary = {
        "total": len(rows),
        "match": 0,
        "mismatch": 0,
        "raw_only": 0,
        "platform_only": 0,
        "incomplete": 0,
    }
    for r in rows:
        summary[r.overall_status.lower()] = summary.get(r.overall_status.lower(), 0) + 1
    return summary