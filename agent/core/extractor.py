"""字段抽取器:从预处理后的数据中抽取 SN/MAC/ICCID/追溯码。

策略:
1. 正则优先 (Deterministic): 按设备的 extraction_rules 抽取
2. LLM 兜底: 正则失败时调用 LLM 解析

输出统一的 DeviceRecord 列表,供后续比对使用。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .preprocessor import PreprocessResult
from .rule_loader import DeviceRules, FieldExtractor

logger = logging.getLogger(__name__)


@dataclass
class DeviceRecord:
    """从原始数据抽取的一条设备记录"""

    sn: Optional[str] = None
    mac: Optional[str] = None
    iccid: Optional[str] = None
    trace_code: Optional[str] = None
    com_port: Optional[str] = None  # 串口(COM94 / COM3)
    trace_response_time: Optional[str] = None
    source_line: Optional[int] = None  # 文本日志中的行号
    confidence: str = "high"  # high / medium / low
    extraction_method: str = "regex"  # regex / llm
    notes: str = ""
    fields_after_anchor: set = field(default_factory=set)  # 哪些字段在锚点之后捕获(成功循环信号)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sn": self.sn,
            "mac": self.mac,
            "iccid": self.iccid,
            "trace_code": self.trace_code,
            "com_port": self.com_port,
            "trace_response_time": self.trace_response_time,
            "source_line": self.source_line,
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "notes": self.notes,
        }

    def is_complete(self, is_4g: bool) -> bool:
        """记录是否完整(可参与比对)。"""
        if is_4g:
            return all([self.sn, self.mac, self.iccid, self.trace_code])
        return all([self.sn, self.mac, self.trace_code])


# === 派生关系(设备配置驱动) ===

def mac_trace_suffix_len(rules: DeviceRules) -> int:
    """读配置的派生长度:MAC 由追溯码后 N 位派生(默认 12)。"""
    fc = rules.field_constraints or {}
    try:
        n = int(fc.get("mac_trace_suffix_len", 12))
    except (TypeError, ValueError):
        n = 12
    return max(4, n)


def mac_derived_from_trace(rules: DeviceRules) -> bool:
    """MAC 是否由追溯码派生(默认 false,设备配置显式开启)。"""
    fc = rules.field_constraints or {}
    return bool(fc.get("mac_derived_from_trace", False))


# === 文本日志抽取 ===

_LINE_TIME_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})")


def extract_from_text_log(
    lines: List[str],
    rules: DeviceRules,
) -> List[DeviceRecord]:
    """从文本日志行抽取设备记录。

    步骤:
    1. 解析所有关键行;trace_code 锚点按「权威形态」唯一化(同码同串口只留一条)
    2. 在每个唯一锚点上绑定 SN/MAC/ICCID:派生优先 + 同串口时间窗兜底
    3. 校验派生关系(配置驱动),校正/标记不一致
    """
    if not lines:
        return []

    # 抽取所有关键行(按设备的 field_extractors)
    parsed = _parse_key_lines(lines, rules)
    if not parsed:
        return []

    # trace_code 锚点唯一化:同(码,串口) 只保留权威形态命中者;无权威形态取首个
    anchors = _unique_trace_anchors(parsed, rules)

    window = rules.extraction_rules.get("session_binding", {}).get("window_seconds", 60)
    records: List[DeviceRecord] = []

    for anchor in anchors:
        if anchor.kind != "trace_code":
            continue

        record = DeviceRecord(
            trace_code=anchor.value,
            com_port=anchor.com_port,
            trace_response_time=anchor.timestamp,
            source_line=anchor.line_number,
        )

        # 绑定 SN/ICCID:同串口 + 窗口内时间最近
        # 绑定方向由设备配置 session_binding.<field>_direction 决定:
        #   after  :字段在 trace 之后读回 → 优先锚点后(防偷上一台旧值)
        #   before :字段在 trace 之前出现 → 优先锚点前(防偷下一台新值)
        #   nearest:不区分前后(就近)
        # SN/ICCID 各自可配(sn_direction / iccid_direction);默认 sn=after(旧行为), iccid=nearest。
        sb = rules.extraction_rules.get("session_binding", {})
        sn_direction = sb.get("sn_direction", "after")
        iccid_direction = sb.get("iccid_direction", "nearest")
        for kind in ("sn", "iccid"):
            direction = sn_direction if kind == "sn" else iccid_direction
            if direction == "before":
                best = _nearest_before_same_port(parsed, anchor, kind, window)
            elif direction == "after":
                best = _nearest_after_same_port(parsed, anchor, kind, window)
            else:
                best = _nearest_same_port(parsed, anchor, kind, window)
            if best:
                setattr(record, kind, best.value)
                if _time_distance_seconds(anchor.timestamp, best.timestamp) is not None and best.timestamp >= anchor.timestamp:
                    record.fields_after_anchor.add(kind)

        # 绑定 MAC:派生优先(前 N 位 == 追溯码末 N 位),找不到再时间窗
        mac = _bind_mac(parsed, anchor, window, rules)
        if mac:
            record.mac = mac

        # 派生校正:MAC 与追溯码不一致时,按派生关系在同串口重找
        if (
            mac_derived_from_trace(rules)
            and record.trace_code
            and record.mac
            and _mac_matches_trace(record.mac, record.trace_code, rules) is False
        ):
            mac_by_suffix = _find_mac_by_trace_suffix(parsed, anchor, record.trace_code, rules)
            if mac_by_suffix:
                record.mac = mac_by_suffix
                record.notes = "MAC 已按追溯码派生关系校正"

        records.append(record)

    # 校验派生关系(校正后仍不一致则标记低置信度)
    if mac_derived_from_trace(rules):
        for r in records:
            if r.trace_code and r.mac:
                if _mac_matches_trace(r.mac, r.trace_code, rules) is False:
                    r.confidence = "low"
                    n = mac_trace_suffix_len(rules)
                    r.notes = f"MAC 与追溯码末{n}位不一致"

    return records


@dataclass
class _KeyLine:
    """一个被识别为关键行的元素"""

    kind: str  # sn / mac / iccid / trace_code
    value: str
    com_port: Optional[str]
    timestamp: Optional[str]
    line_number: int
    raw: str


def _parse_key_lines(lines: List[str], rules: DeviceRules) -> List[_KeyLine]:
    """扫描所有行,按设备的 field_extractors 识别关键字段行。

    通用实现:不再硬编码 A02FG 的正则,而是读取 rules.field_extractors。
    每个字段优先用 context_anchor(带字面上下文,避免误匹配),取 group 1 为字段值;
    无锚点或锚点不命中时,回退到 regex 直接搜索。
    """
    results: List[_KeyLine] = []
    for i, line in enumerate(lines, start=1):
        for field_name, ext in rules.field_extractors.items():
            value = _extract_field_value(ext, line)
            if value is None:
                continue
            results.append(
                _KeyLine(
                    kind=field_name,
                    value=value,
                    com_port=_extract_com(line),
                    timestamp=_extract_timestamp(line),
                    line_number=i,
                    raw=line.rstrip(),
                )
            )
    return results


def _extract_field_value(ext: FieldExtractor, line: str) -> Optional[str]:
    """按 FieldExtractor 从一行中抽取「字段值」(而非标签)。

    取值优先级:
    1. context_anchor 带捕获组 → group(1)。这是 A02FG 的约定:锚点里用括号把「值」包起来,
       如 ``COM\\d+:收mac:([0-9A-F]{12})``。
    2. regex 带捕获组 → group(1)。这是部分 LLM 生成的另一种约定:值捕获放在 regex,
       锚点只是前缀,如 ``context_anchor="COM..:mac:", regex="COM..:mac:([0-9A-F]{12})"``。
    3. regex 无捕获组 → group(0)(裸值模式,如 ``[0-9A-F]{12}``)。

    关键约束:context_anchor 若「没有」捕获组,它只是定位用的前缀,绝不能把整个前缀
    (如 ``COM77:收 :mac:``)当作字段值返回——这正是「交付物出现 COM口/字段名」的根因。
    """
    # 1. context_anchor 且带捕获组 → 取捕获值(最权威,带上下文)
    if ext.compiled_anchor is not None and ext.compiled_anchor.groups >= 1:
        m = ext.compiled_anchor.search(line)
        if m and m.lastindex:
            return _apply_post_process(m.group(1), ext, line)

    # 2. regex(带捕获组取 group 1,无捕获组取整体匹配)
    if ext.compiled_regex is not None:
        m = ext.compiled_regex.search(line)
        if m:
            value = m.group(1) if (m.lastindex and m.group(1) is not None) else m.group(0)
            return _apply_post_process(value, ext, line)

    return None


def _apply_post_process(value: str, ext: FieldExtractor, line: Optional[str] = None) -> str:
    """应用 post_process(trim/upper/lower),并统一大写(字段均为十六进制标识)。

    若 ext 标记 sanitize_duplicates(如 ICCID 上位机异常重复打印),对粘连串去重。
    line 提供原始行,便于从「完整粘连串」中取最后一个完整号(正则可能只捕获到
    第一个号或被 '?' 截断的前缀)。
    """
    value = value.strip()
    if getattr(ext, "sanitize_duplicates", False):
        value = _dedupe_repeated(value, line)
    for step in ext.post_process:
        if step == "trim":
            value = value.strip()
        elif step == "upper":
            value = value.upper()
        elif step == "lower":
            value = value.lower()
    return value.upper()


def _dedupe_repeated(s: str, line: Optional[str] = None) -> str:
    """从粘连串中提取「最后一个完整值」。

    上位机异常时会把 ICCID 打印成粘连串,常见形态:
    - 缺省前缀型: '89860468152590218986' + '0468152590219181' → 第二个号缺 8986 前缀
    - 显式重复型: '89860468152590219559' + '89860468152590219559' → 两个完整号
    - 脏分隔型  : '89860468152590219?89860468152590219741' → 中间夹 '?',首段被截断
    实测「最后一个完整号」才是当前设备真值(平台/旧逻辑取的是第一个=脏前缀),
    因此扫描整行中所有完整的 19-20 位 8986 号并返回最后一个。

    ICCID 可能是 hex(如 A02SG 的 '898604781025D0538986' 含 'D')——收集时按
    [0-9A-Fa-f] 而非 isdigit(),避免 hex 号内部的 '8986' 子串被误判为「重复开头」。
    扫描规则:从每个 '8986' 位置收集其后连续 hex,长度 >= 19 即视为一个完整号候选
    (取前 20 位),返回最后一个;找不到任何完整号时原样返回 s,不再按「重复前缀」截断
    (旧退化逻辑会把 hex 号内部 '...D0538986' 的 '8986' 当成重复标记,截断成 16 位)。
    """
    src = line if line else s
    marker = "8986"
    candidates: List[str] = []
    i = src.find(marker)
    while i != -1:
        # 收集 '8986' 后连续 hex 字符的完整号长度(4 位前缀 + hex 位数)
        j = i + 4
        while j < len(src) and src[j] in "0123456789ABCDEFabcdef":
            j += 1
        total_len = j - i
        if total_len >= 19:
            candidates.append(src[i:i + 20])
        i = src.find(marker, i + 1)
    if candidates:
        return candidates[-1]
    return s


def _extract_timestamp(line: str) -> Optional[str]:
    """从行首抽取 HH:MM:SS。"""
    m = _LINE_TIME_RE.match(line)
    return m.group(1) if m else None


def _extract_com(line: str) -> Optional[str]:
    """抽取 COMxx 中的 xx。"""
    m = re.search(r"COM(\d+)", line)
    return m.group(1) if m else None


def _within_window(t1: Optional[str], t2: Optional[str], window_seconds: int) -> bool:
    """判断两个时间戳是否在 window_seconds 秒内。"""
    if not t1 or not t2:
        return True  # 无法判定时默认在窗口内
    try:
        h1, m1, s1 = (int(x) for x in t1.split(":"))
        h2, m2, s2 = (int(x) for x in t2.split(":"))
        sec1 = h1 * 3600 + m1 * 60 + s1
        sec2 = h2 * 3600 + m2 * 60 + s2
        return abs(sec1 - sec2) <= window_seconds
    except (ValueError, AttributeError):
        return True


def _time_distance_seconds(t1: Optional[str], t2: Optional[str]) -> int:
    """两个 HH:MM:SS 时间戳的绝对秒差;无法解析时返回一个很大的值(视为不近)。"""
    if not t1 or not t2:
        return 10 ** 9
    try:
        h1, m1, s1 = (int(x) for x in t1.split(":"))
        h2, m2, s2 = (int(x) for x in t2.split(":"))
        return abs((h1 * 3600 + m1 * 60 + s1) - (h2 * 3600 + m2 * 60 + s2))
    except (ValueError, AttributeError):
        return 10 ** 9


def _nearest_same_port(
    parsed: List[_KeyLine],
    anchor: _KeyLine,
    kind: str,
    window_seconds: int,
) -> Optional[_KeyLine]:
    """返回与锚点「同串口 + 窗口内时间最近」的一条 kind 关键行(无则 None)。

    取窗口内时间最近(不区分前后);跨循环错绑问题由「锚点唯一化选成功循环」解决。
    """
    return _nearest_in_window(parsed, anchor, kind, window_seconds)


def _nearest_after_same_port(
    parsed: List[_KeyLine],
    anchor: _KeyLine,
    kind: str,
    window_seconds: int,
) -> Optional[_KeyLine]:
    """返回与锚点「同串口 + 窗口内 + 锚点之后时间最近」的一条 kind 关键行(无则 None)。

    优先选「锚点之后」出现的行:物理时序上 SN(id)通常在 trace code 之后读回;
    失败循环的上一台设备旧 SN 恰好在锚点之前(无 after 时才会被「就近」误绑)。
    有 after 候选时取最近的 after;无 after 时回退到窗口内最近的(含 before)。
    """
    best = _nearest_in_window(parsed, anchor, kind, window_seconds, after_only=True)
    if best is not None:
        return best
    return _nearest_in_window(parsed, anchor, kind, window_seconds)


def _nearest_before_same_port(
    parsed: List[_KeyLine],
    anchor: _KeyLine,
    kind: str,
    window_seconds: int,
) -> Optional[_KeyLine]:
    """返回与锚点「同串口 + 窗口内 + 锚点之前时间最近」的一条 kind 关键行(无则 None)。

    A02SG/A02EG/A02FG 时序与 F16G 相反:SN 是板检开始发的「发:568121440200」指令
    (含 SN),在回复追溯码(锚点)之前。after-优先会跳过本设备 SN(锚点前)而选到
    下一台设备的 SN(锚点后)→ 误判。before-优先取锚点前最近的 SN(本设备真值),
    失败循环的下一台设备 SN 恰在锚点之后不会被偷。
    """
    best: Optional[_KeyLine] = None
    best_d: Optional[int] = None
    for k in parsed:
        if k.kind != kind or k.com_port != anchor.com_port:
            continue
        if not _within_window(anchor.timestamp, k.timestamp, window_seconds):
            continue
        if k.timestamp > anchor.timestamp:
            continue
        d = _time_distance_seconds(anchor.timestamp, k.timestamp)
        if best_d is None or d < best_d:
            best_d = d
            best = k
    if best is not None:
        return best
    return _nearest_in_window(parsed, anchor, kind, window_seconds)


def _nearest_in_window(
    parsed: List[_KeyLine],
    anchor: _KeyLine,
    kind: str,
    window_seconds: int,
    after_only: bool = False,
) -> Optional[_KeyLine]:
    best: Optional[_KeyLine] = None
    best_d: Optional[int] = None
    for k in parsed:
        if k.kind != kind:
            continue
        if k.com_port != anchor.com_port:
            continue
        if not _within_window(anchor.timestamp, k.timestamp, window_seconds):
            continue
        if after_only and k.timestamp < anchor.timestamp:
            continue
        d = _time_distance_seconds(anchor.timestamp, k.timestamp)
        if best_d is None or d < best_d:
            best_d = d
            best = k
    return best


def _record_completeness(r: DeviceRecord, is_4g: bool = True) -> int:
    """记录字段完整度(用于择优)。"""
    fields = ["sn", "mac", "trace_code"] + (["iccid"] if is_4g else [])
    return sum(1 for f in fields if getattr(r, f))


def _unique_trace_anchors(parsed: List[_KeyLine], rules: DeviceRules) -> List[_KeyLine]:
    """trace_code 锚点唯一化:同(追溯码,串口) 合并为一条锚点。

    日志里同一条 trace code 常有多种形态(「发: trace code:」「收 :trace code:」)，
    且一台设备可能多次重测(同码同串口出现多个发:锚点)。
    策略:
    1. 按 (码, 串口) 分组,取该组里「权威形态(发:)」的锚点;
    2. 若该组有多个发:锚点(多次重测),取「字段上下文最全」的那条 ——
       优先选附近(同串口、时间窗内)有 id/mac/iccid 关键行的,避免选了只带粘连残缺值的那次循环。
    3. 无权威形态时取首个。
    """
    ext_cfg = rules.extraction_rules.get("field_extractors", {}).get("trace_code", {}) or {}
    forms = ext_cfg.get("authoritative_forms") or []
    window = rules.extraction_rules.get("session_binding", {}).get("window_seconds", 60)

    def _matches_form(line: str) -> bool:
        if not forms:
            return False
        return any(re.search(f, line) is not None for f in forms if f)

    buckets: Dict[tuple, List[_KeyLine]] = {}
    for k in parsed:
        if k.kind != "trace_code":
            continue
        buckets.setdefault((k.value, k.com_port), []).append(k)

    chosen: Dict[tuple, _KeyLine] = {}
    for key, group in buckets.items():
        cand = [k for k in group if _matches_form(k.raw)] or group
        # 多个候选(多次重测):取字段上下文最全者
        if len(cand) > 1:
            cand = sorted(
                cand,
                key=lambda k: _anchor_context_score(parsed, k, window),
                reverse=True,
            )
        chosen[key] = cand[0]

    order = {id(k): i for i, k in enumerate(parsed) if k.kind == "trace_code"}
    return sorted(chosen.values(), key=lambda k: order.get(id(k), 0))


def _anchor_context_score(parsed: List[_KeyLine], anchor: _KeyLine, window: int) -> int:
    """锚点候选的「字段上下文完整度」:同串口、时间窗内有多少条不同 kind 的关键行。

    成功循环信号:SN/ICCID 出现在锚点(发 trace)之后 → 额外加权。
    失败循环常常窗内只有「上一个设备的旧 id」,无 after-anchor id。
    """
    kinds: set = set()
    after_id = 0
    for k in parsed:
        if k.kind == "trace_code" or k.com_port != anchor.com_port:
            continue
        if _within_window(anchor.timestamp, k.timestamp, window):
            kinds.add(k.kind)
            if k.timestamp >= anchor.timestamp and k.kind in ("sn", "iccid"):
                after_id += 1
    return len(kinds) * 10 + after_id


def _bind_mac(
    parsed: List[_KeyLine],
    anchor: _KeyLine,
    window_seconds: int,
    rules: DeviceRules,
) -> Optional[str]:
    """绑定 MAC:派生优先(前 N 位 == 追溯码末 N 位),找不到再同串口时间窗兜底。"""
    candidates = [
        k for k in parsed
        if k.kind == "mac" and k.com_port == anchor.com_port
    ]
    if not candidates:
        return None

    if mac_derived_from_trace(rules) and anchor.value:
        n = mac_trace_suffix_len(rules)
        suffix = anchor.value[-n:].upper()
        for k in candidates:
            if k.value[:n].upper() == suffix:
                return k.value
    # 时间窗兜底
    best = _nearest_same_port(parsed, anchor, "mac", window_seconds)
    return best.value if best else None


def _mac_matches_trace(mac: Optional[str], trace_code: Optional[str], rules: DeviceRules) -> Optional[bool]:
    """MAC 是否与追溯码派生一致(读配置派生长度)。"""
    if not mac or not trace_code:
        return None
    n = mac_trace_suffix_len(rules)
    if len(mac) < n or len(trace_code) < n:
        return False
    return mac[:n].upper() == trace_code[-n:].upper()


def _find_mac_by_trace_suffix(
    parsed: List[_KeyLine],
    anchor: _KeyLine,
    trace_code: str,
    rules: DeviceRules,
) -> Optional[str]:
    """按派生关系在同串口查找 MAC(确定性校正)。"""
    n = mac_trace_suffix_len(rules)
    suffix = trace_code[-n:].upper()
    for k in parsed:
        if k.kind != "mac" or k.com_port != anchor.com_port:
            continue
        if k.value[:n].upper() == suffix:
            return k.value
    return None


# === 数据库 / CSV 抽取 ===

def extract_from_records(
    records: List[Dict[str, Any]],
    rules: DeviceRules,
) -> List[DeviceRecord]:
    """从 SQLite 表或 CSV 记录中按列名直接映射。

    适用场景: 平台记录本身就是结构化的(每行一台设备,列名已知)
    """
    results: List[DeviceRecord] = []
    if not records:
        return results

    # 用 platform_csv_columns 的映射来定位列
    cols = rules.platform_csv_columns
    sn_col = cols.get("sn", "sn")
    mac_col = cols.get("mac", "mac")
    iccid_col = cols.get("iccid", "iccid")
    trace_col = cols.get("trace_code", "trace_code")

    for row in records:
        rec = DeviceRecord(
            sn=_normalize_value(row.get(sn_col)),
            mac=_normalize_value(row.get(mac_col)),
            iccid=_normalize_value(row.get(iccid_col)) if rules.is_4g else None,
            trace_code=_normalize_value(row.get(trace_col)),
            extraction_method="direct",
            confidence="high",
        )
        results.append(rec)
    return results


def _normalize_value(v: Any) -> Optional[str]:
    """把任意值规范化为字符串。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    return s.upper() if re.match(r"^[0-9A-Fa-f]+$", s) else s


# === LLM 兜底抽取 ===

def extract_with_llm_fallback(
    text_log_lines: List[str],
    regex_records: List[DeviceRecord],
    rules: DeviceRules,
) -> List[DeviceRecord]:
    """当正则抽取遗漏或失败时,调用 LLM 解析剩余的歧义行。

    Args:
        text_log_lines: 原始保留行
        regex_records:  正则已抽到的记录
        rules:          设备规则

    仅当设备规则的 llm_fallback.enabled 为 true、且已配置 API Key 时才生效;
    只对正则未抽全的记录发起 LLM 调用,并用调用结果补全字段。
    """
    fb = rules.extraction_rules.get("llm_fallback", {}) or {}
    if not fb.get("enabled", False):
        return regex_records
    import os
    if os.environ.get("AGENT_DISABLE_LLM"):
        return regex_records

    from ..llm.client import create_default_client
    from ..llm.parser import parse_json_response

    client = create_default_client()
    if client is None:
        logger.info("LLM fallback skipped: 未配置 API Key")
        return regex_records

    field_names = ["sn", "mac", "iccid", "trace_code"] if rules.is_4g else ["sn", "mac", "trace_code"]
    incomplete = [r for r in regex_records if not r.is_complete(rules.is_4g)]
    if not incomplete:
        return regex_records

    max_calls = int(fb.get("max_calls", 20))
    radius = int(fb.get("context_radius", 40))
    device_rules = _device_rules_snippet(rules)

    filled = 0
    for rec in incomplete[:max_calls]:
        missing = [f for f in field_names if not getattr(rec, f)]
        context = _grab_context(text_log_lines, rec.source_line, radius)
        fields = _llm_fill_fields(client, rules, device_rules, rec, missing, context)
        if not fields:
            continue
        changed = False
        for fname in field_names:
            val = fields.get(fname)
            if val and not getattr(rec, fname):
                val = str(val).strip().upper()
                # 硬性约束校验,防止 LLM 编造出格式不合法的值
                if _valid_field_value(fname, val, rec.trace_code, rules):
                    setattr(rec, fname, val)
                    changed = True
        if changed:
            rec.extraction_method = "llm"
            rec.confidence = fields.get("confidence") or "medium"
            filled += 1
    if filled:
        logger.info("LLM fallback 补全 %d/%d 条记录", filled, len(incomplete))
    return regex_records


def _grab_context(lines: List[str], source_line: Optional[int], radius: int) -> str:
    """取某条记录锚点行附近的前后 radius 行作为 LLM 上下文。"""
    if not lines:
        return ""
    if not source_line:
        return "\n".join(lines[: radius * 2])
    start = max(0, source_line - 1 - radius)
    end = min(len(lines), source_line - 1 + radius + 1)
    return "\n".join(lines[start:end])


def _valid_field_value(field_name: str, val: str, trace_code: Optional[str], rules: DeviceRules) -> bool:
    """校验 LLM 补全的字段值是否满足硬性约束(防止编造)。"""
    if not val:
        return False
    fc = rules.field_constraints or {}
    if field_name == "sn":
        fmt = fc.get("sn_format")
        if fmt:
            return bool(re.fullmatch(fmt, val, re.IGNORECASE))
        return True
    if field_name == "mac":
        # 若有追溯码,要求后8位一致(设备固化派生关系)
        if trace_code and len(val) >= 8 and len(trace_code) >= 8:
            return val[-8:].upper() == trace_code[-8:].upper()
        return True
    if field_name == "iccid":
        starts = fc.get("iccid_starts_with")
        if starts and not val.upper().startswith(starts.upper()):
            return False
        lo, hi = fc.get("iccid_length", [0, 999])
        return lo <= len(val) <= hi
    if field_name == "trace_code":
        return True
    return True


def _device_rules_snippet(rules: DeviceRules) -> str:
    """从设备 prompts.md 中抽取规则片段(去代码块,限制长度)。"""
    if not rules.prompts_md:
        return ""
    import re as _re

    text = _re.sub(r"```.*?```", "", rules.prompts_md, flags=_re.S)
    return text.strip()[:1500]


def _llm_fill_fields(client, rules, device_rules, rec, missing, context) -> Dict[str, Any]:
    """调用 LLM 补全单条记录的缺失字段,失败时返回空字典。"""
    from ..llm.parser import parse_json_response
    from ..llm.prompts import SYSTEM_ROLE_BASE

    system = (SYSTEM_ROLE_BASE + "\n\n## 设备专属规则\n" + device_rules).strip()
    user = (
        f"该设备追溯码为 {rec.trace_code},串口 COM{rec.com_port or '?'}。\n"
        f"正则已抽取到: SN={rec.sn}, MAC={rec.mac}, ICCID={rec.iccid}。\n"
        f"需要补全的字段: {', '.join(missing)}\n\n"
        "## 板检日志片段(该设备测试循环附近)\n```text\n"
        f"{context[:8000]}\n```\n\n"
        '只输出 JSON,格式: {"sn": "...", "mac": "...", "iccid": "...", '
        '"trace_code": "...", "confidence": "high|medium|low"}\n'
        "无法确定的字段输出 null,不要编造值。"
    )
    try:
        resp = client.complete(
            system_prompt=system,
            user_prompt=user,
            max_tokens=1024,
            temperature=0.0,
        )
        data = parse_json_response(resp.text)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning("LLM fallback 抽取失败: %s", e)
    return {}