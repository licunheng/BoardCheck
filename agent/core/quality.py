"""数据质量层:归一去重 + 唯一性硬校验 + 组内权威值仲裁。

在「字段抽取」之后、「平台比对」之前执行,纯确定性,不依赖 LLM,秒级完成。
解决两类问题:
1. 同一追溯码因日志多次循环测试 / 多形态行而重复抽取 → 归一去重。
2. 核心字段(SN/MAC/ICCID)与追溯码必须一一对应,多对一即错绑 → 硬校验标记。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .extractor import DeviceRecord
from .rule_loader import DeviceRules


def dedupe_and_validate(
    records: List[DeviceRecord], rules: DeviceRules
) -> Tuple[List[DeviceRecord], Dict[str, Any]]:
    """按追溯码归一去重,并做 1:1 唯一性硬校验与 ICCID 长度校验。

    Returns:
        (cleaned_records, quality_report)
        quality_report: {"deduped": int, "conflicts": int, "truncated_iccid": int, "details": [...]}
    """
    if not records:
        return [], _empty_report()

    # 1. 按追溯码归组(追溯码为设备唯一主键)
    groups: Dict[str, List[DeviceRecord]] = defaultdict(list)
    for r in records:
        tc = (r.trace_code or "").strip().upper()
        if tc:
            groups[tc].append(r)
        else:
            # 无追溯码的记录无法归组,单独保留(比对阶段会按 RAW_ONLY 处理)
            groups[f"__notc_{id(r)}"].append(r)

    details: List[str] = []
    deduped_total = 0
    cleaned: List[DeviceRecord] = []

    # 2. 组内归并:每台设备只留一条权威记录
    for tc, group in groups.items():
        if tc.startswith("__notc_"):
            cleaned.extend(group)
            continue

        if len(group) > 1:
            deduped_total += len(group) - 1

        canonical = _pick_canonical(group)
        # 组内多值 → 冲突标记(不静默选一个,注明分歧)
        _flag_group_conflicts(canonical, group, details)
        cleaned.append(canonical)

    # 3. 跨设备 1:1 唯一性硬校验(同一字段值不得对应多个追溯码)
    conflict_count = _flag_cross_device_conflicts(cleaned, details)

    # 4. ICCID 长度校验(截断/超长)
    truncated = _flag_iccid_length(cleaned, rules, details)

    report = {
        "deduped": deduped_total,
        "conflicts": conflict_count,
        "truncated_iccid": truncated,
        "details": details,
        "raw_count": len(records),
        "cleaned_count": len(cleaned),
    }
    return cleaned, report


def _empty_report() -> Dict[str, Any]:
    return {
        "deduped": 0,
        "conflicts": 0,
        "truncated_iccid": 0,
        "details": [],
        "raw_count": 0,
        "cleaned_count": 0,
    }


def _pick_canonical(group: List[DeviceRecord]) -> DeviceRecord:
    """在一组同追溯码记录中，取「字段最完整、派生一致」的记录为权威，缺失字段用众数补齐。

    同一设备可能多次重测(同码不同串口/不同循环)，其中只有「成功循环」字段齐全；
    直接取众数可能在两个不同值各出现 1 次时误取第一个。这里先按完整度+置信度择优，
    再对缺失字段用组内众数补齐。
    """
    if len(group) == 1:
        return group[0]

    # 择优:字段完整度高的优先;同完整度时 confidence 更可信优先
    def _score(r: DeviceRecord) -> tuple:
        filled = sum(1 for f in ("sn", "mac", "iccid", "trace_code") if getattr(r, f))
        conf = {"high": 0, "medium": 1, "low": 2}.get((r.confidence or "medium"), 1)
        # 派生一致性:MAC 与追溯码一致是成功循环的强信号(仅当追溯码存在)
        derived_ok = 0
        if r.trace_code and r.mac:
            derived_ok = 1 if r.mac[:12].upper() == r.trace_code[-12:].upper() else 0
        # 字段在锚点之后捕获(成功循环信号:SN 在 trace 之后出现)
        after_ok = 1 if "sn" in getattr(r, "fields_after_anchor", set()) else 0
        return (filled, after_ok, derived_ok, -conf)

    best = max(group, key=_score)
    # 显式选择:选 after_anchor 信号更强的记录作为权威源
    # (max 在 (filled, after_ok, derived_ok, -conf) 全平手时取第一个;
    #  但 146 的 after_ok=1 > 119 的 0,不应平手)
    canonical_src = max(group, key=lambda r: (
        sum(1 for f in ("sn", "mac", "iccid", "trace_code") if getattr(r, f)),
        1 if "sn" in getattr(r, "fields_after_anchor", set()) else 0,
        1 if (r.trace_code and r.mac and r.mac[:12].upper() == r.trace_code[-12:].upper()) else 0,
    ))
    canonical = DeviceRecord(
        trace_code=canonical_src.trace_code,
        com_port=canonical_src.com_port,
        trace_response_time=canonical_src.trace_response_time,
        source_line=canonical_src.source_line,
        confidence=canonical_src.confidence,
        extraction_method=canonical_src.extraction_method,
        notes=canonical_src.notes or "",
        sn=canonical_src.sn,
        mac=canonical_src.mac,
        iccid=canonical_src.iccid,
        fields_after_anchor=set(getattr(canonical_src, "fields_after_anchor", set())),
    )
    # 缺失字段用组内众数补齐(只补空缺,不覆盖已择优的值)
    for field in ("sn", "mac", "iccid"):
        if getattr(canonical, field):
            continue
        val, _ = _majority([getattr(r, field) for r in group])
        setattr(canonical, field, val)
    return canonical


def _majority(values: List[Optional[str]]) -> Tuple[Optional[str], List[str]]:
    """返回 (众数值, 去重后的所有不同值列表)。"""
    non_null = [str(v).strip().upper() for v in values if v]
    if not non_null:
        return None, []
    counter = Counter(non_null)
    top_val, _ = counter.most_common(1)[0]
    return top_val, list(counter.keys())


def _flag_group_conflicts(
    canonical: DeviceRecord, group: List[DeviceRecord], details: List[str]
) -> None:
    """同一追溯码组内出现多个不同字段值时,标记冲突(记录分歧,不静默丢弃)。"""
    if len(group) < 2:
        return
    tc = canonical.trace_code or "?"
    for field in ("sn", "mac", "iccid"):
        _, distinct = _majority([getattr(r, field) for r in group])
        if len(distinct) > 1:
            note = f"{field} 组内冲突:{' / '.join(distinct)}"
            canonical.notes = _append_note(canonical.notes, note)
            canonical.confidence = "low"
            details.append(f"追溯码 {tc} 的 {field} 出现 {len(distinct)} 个不同值: {' / '.join(distinct)}")


def _flag_cross_device_conflicts(cleaned: List[DeviceRecord], details: List[str]) -> int:
    """跨设备 1:1 唯一性校验:同一 SN/MAC/ICCID 不得对应多个追溯码。"""
    # 只对有追溯码的记录做校验
    index: Dict[str, Dict[str, set]] = {"sn": {}, "mac": {}, "iccid": {}}
    for r in cleaned:
        tc = (r.trace_code or "").strip().upper()
        if not tc:
            continue
        for field in ("sn", "mac", "iccid"):
            val = getattr(r, field)
            if not val:
                continue
            key = val.strip().upper()
            index[field].setdefault(key, set()).add(tc)

    conflict_count = 0
    flagged: Dict[str, set] = {"sn": set(), "mac": set(), "iccid": set()}
    for field in ("sn", "mac", "iccid"):
        for val, tcs in index[field].items():
            if len(tcs) > 1:
                conflict_count += 1
                details.append(
                    f"{field.upper()} {val} 对应 {len(tcs)} 个追溯码({', '.join(sorted(tcs))}),"
                    f"违反一一对应约束,疑似绑定错误"
                )
                flagged[field].add(val)

    # 给涉及的记录打低置信度标记
    for r in cleaned:
        tc = (r.trace_code or "").strip().upper()
        if not tc:
            continue
        for field in ("sn", "mac", "iccid"):
            val = getattr(r, field)
            if not val:
                continue
            if val.strip().upper() in flagged[field]:
                note = f"{field} 违反唯一性约束(与其他追溯码重复)"
                r.notes = _append_note(r.notes, note)
                if r.confidence != "low":
                    r.confidence = "low"
    return conflict_count


def _flag_iccid_length(
    cleaned: List[DeviceRecord], rules: DeviceRules, details: List[str]
) -> int:
    """校验 ICCID 长度(默认 19~20 位,可被 field_constraints.iccid_length 覆盖)。"""
    fc = rules.field_constraints or {}
    lo, hi = fc.get("iccid_length", [19, 20])
    truncated = 0
    for r in cleaned:
        if not r.iccid:
            continue
        iccid = r.iccid.strip().upper()
        if not (lo <= len(iccid) <= hi):
            truncated += 1
            r.notes = _append_note(r.notes, f"ICCID 长度异常({len(iccid)}位,应为 {lo}~{hi} 位)")
            if r.confidence != "low":
                r.confidence = "low"
            details.append(f"追溯码 {r.trace_code or '?'} 的 ICCID {iccid} 长度异常({len(iccid)}位)")
    return truncated


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    if note in existing:
        return existing
    return (existing + "; " + note).strip("; ")


# === 供比对器复用的字段等价判断 ===

def field_equivalent(platform_val: Optional[str], raw_val: Optional[str]) -> Optional[bool]:
    """判断平台值(标准)与日志值(可能带头/尾码)是否指向同一字段。

    - 双方缺失 → None
    - 精确相等 → True
    - 平台值是日志值的子串(日志值被头码/尾码包裹)→ True
    - 否则 → False

    包含匹配做长度下限保护,避免短值误命中。
    """
    p = _norm(platform_val)
    r = _norm(raw_val)
    if not p or not r:
        return None
    if p == r:
        return True
    # 头/尾码包裹:平台标准值嵌入在日志值内部(如 平台SN=fa00..0050,
    # 日志 id=12345fa00..00506789)
    if len(p) >= 6 and p in r:
        return True
    return False


def _norm(v: Optional[str]) -> str:
    if v is None:
        return ""
    return str(v).strip().upper()


def _valid_hex_or_digits(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Fa-f]+", s))
