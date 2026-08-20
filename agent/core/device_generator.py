"""设备配置自动生成器(提示词输入 → 多次深度调用 LLM → 通用可复用配置)。

用户输入:
- device_code:   新设备代号(如 B01X)
- description:   该产品板检规则的自然语言描述(检索规则)
- sample_log:    (可选)板检日志样例文本
- sample_csv:    (可选)平台 CSV 样例
- is_4g_hint:    (可选)强制指定是否 4G 设备

流程(五阶段,深度思考,不省 token/调用次数,准确性第一):
1. 阶段 A 形态穷举: 让模型用自然语言穷举日志里每种字段行形态、派生关系、头尾码、绑定原则(自由输出思考,不套 JSON)
2. 阶段 B 字段规格: 结构化归纳(唯一标识=追溯码、派生=MAC←追溯码、稳定=SN/ICCID、头尾码剥离规则)
3. 阶段 C 生成四件套: 参照 A02FG 模板产出 device.json / filter_rules.json / extraction_rules.json / prompts.md
4. 阶段 D 硬质量自检: 样例试抽 + 抽全率 + 去重率 + 1:1 唯一性 + 平台列名
5. 阶段 E 迭代修正: 任一质量门槛不达标就带自检报告回炉修正,循环直到达标或达上限

产出写入 agent/devices/<device_code>/,与 A02FG 同构,立即成为可复用的通用配置。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import DEVICES_DIR, get_user_devices_dir
from ..llm.client import create_default_client
from ..llm.parser import parse_json_response
from .rule_loader import DeviceRules, device_exists

logger = logging.getLogger(__name__)

_SAMPLE_LOG_MAX = 30000
_SAMPLE_CSV_MAX = 8000

# 合法的设备代号(字母数字 + 下划线/中划线)
_DEVICE_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class DeviceConfigError(RuntimeError):
    """设备配置生成失败。"""


def _report(cb: Optional[Callable[[str], None]], msg: str) -> None:
    """向进度回调发送消息(忽略回调自身异常,不影响主流程)。"""
    if cb is None:
        return
    try:
        cb(msg)
    except Exception:
        pass


def generate_device_config(
    device_code: str,
    description: str,
    sample_log: str = "",
    sample_csv: str = "",
    is_4g_hint: Optional[bool] = None,
    overwrite: bool = False,
    client=None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """生成一套新设备配置并写入 devices/<code>/。

    返回结果字典,含生成过程统计与自检报告。
    progress_callback(可选) 在每个阶段开始时收到一条中文进度消息,供后台任务轮询展示。
    """
    device_code = (device_code or "").strip()
    description = (description or "").strip()
    if not device_code:
        raise DeviceConfigError("设备代号不能为空")
    if not _DEVICE_CODE_RE.match(device_code):
        raise DeviceConfigError(
            f"设备代号 {device_code!r} 不合法(仅允许字母数字与 _ - ,且以字母数字开头)"
        )
    if not description:
        raise DeviceConfigError("请提供该产品板检规则的自然语言描述")
    if device_exists(device_code) and not overwrite:
        raise DeviceConfigError(
            f"设备 {device_code!r} 已存在。如需覆盖,请勾选「覆盖已有配置」。"
        )

    if client is None:
        client = create_default_client()
    if client is None:
        raise DeviceConfigError(
            "未配置 LLM API Key,无法自动生成配置。请先在「LLM 设置」中填入并测试连接。"
        )
    if os.environ.get("AGENT_DISABLE_LLM"):
        raise DeviceConfigError("已通过 AGENT_DISABLE_LLM 禁用 LLM,无法自动生成配置。")

    calls = 0

    # 阶段 A: 日志形态穷举(自由文本深度思考,不套 JSON,不省 token)
    _report(progress_callback, "阶段 A/5: 穷举日志行形态(深度思考,推理模型可能需 2~5 分钟)")
    morphology = _analyze_morphology(
        client, device_code, description, sample_log, sample_csv, is_4g_hint
    )
    calls += 1

    # 阶段 A2: 派生关系实测(确定性 + LLM 兜底)——平台为准,数出 mac/sn 派生关系与头尾码
    _report(progress_callback, "阶段 A2/5: 实测派生关系与头尾码(平台 CSV ↔ 日志)")
    derived = _measure_derived_relations(sample_csv, sample_log, client, device_code)
    calls += 1 if derived.get("llm_used") else 0

    # 阶段 B: 字段规格(结构化,强制明确 唯一标识/派生/稳定字段)
    _report(progress_callback, "阶段 B/5: 归纳字段规格(唯一标识·派生关系·头尾码剥离)")
    spec = _build_spec(
        client, device_code, description, sample_log, sample_csv, is_4g_hint, morphology, derived
    )
    calls += 1
    if is_4g_hint is not None:
        spec["is_4g"] = bool(is_4g_hint)

    # 阶段 C: 生成四件套(结构校验不通过则带反馈重试,最多 3 次)
    _report(progress_callback, "阶段 C/5: 参照 A02FG 模板生成四件套配置")
    config: Dict[str, Any] = {}
    feedback = ""
    problems: List[str] = []
    for attempt in range(3):
        config = _generate_config(
            client, device_code, description, spec, morphology, sample_log, sample_csv, feedback
        )
        calls += 1
        config = _normalize_config(config)
        problems = _validate_config_structure(config)
        if not problems:
            break
        feedback = (
            "上一轮生成的四件套结构有误,问题:" + "; ".join(problems)
            + "。请务必让 extraction_rules_json 顶层直接包含 field_extractors"
            "(形如 {\"version\":..., \"field_extractors\": {...}},不要写成 {\"extraction_rules\": {...}}),"
            "filter_rules_json 顶层直接包含 keep_patterns/drop_patterns,不要额外嵌套一层。"
        )
        _report(progress_callback, f"阶段 C/5: 结构校验未通过,第 {attempt + 1} 次修正重试")
    if problems:
        raise DeviceConfigError("多次生成后配置结构仍不合法:" + "; ".join(problems))

    # 写入文件
    rules_dir = _write_device_files(device_code, config)

    # 阶段 D: 硬质量自检(正则编译 + 样例试抽 + 去重率 + 唯一性 + 平台列名 + 值合法性)
    _report(progress_callback, "阶段 D/5: 硬质量自检(抽全率/重复率/唯一性/值合法性)")
    preview = _validate_and_preview(device_code, rules_dir, sample_log, sample_csv)

    # 阶段 E: 迭代修正循环(达标才交付,不省调用次数;上限放宽到 5 轮)
    max_rounds = 5
    round_no = 0
    fixes: Dict[str, Any] = {}
    while preview.get("needs_fix") and round_no < max_rounds:
        round_no += 1
        _report(progress_callback, f"阶段 E/5: 第 {round_no}/{max_rounds} 轮修正正则…")
        fixed = _fix_regexes(client, device_code, config, sample_log, preview, morphology, spec)
        calls += 1
        if not fixed:
            break
        _write_device_files(device_code, fixed, rules_dir=rules_dir)
        preview = _validate_and_preview(device_code, rules_dir, sample_log, sample_csv)
        fixes = fixed
    if preview.get("needs_fix"):
        _report(
            progress_callback,
            "自检未完全达标,已写入配置但仍建议人工核对(见「待人工复核」列表)",
        )

    if preview.get("needs_fix"):
        _report(
            progress_callback,
            "自检未完全达标,已写入配置但仍建议人工核对(见「待人工复核」列表)",
        )

    return {
        "device_code": device_code,
        "display_name": config.get("device_json", {}).get("display_name", device_code),
        "is_4g": config.get("device_json", {}).get("is_4g", False),
        "calls": calls,
        "rules_dir": str(rules_dir),
        "files": [
            "device.json",
            "filter_rules.json",
            "extraction_rules.json",
            "prompts.md",
        ],
        "preview": preview,
        "fixed": bool(fixes),
    }


# === 阶段 A: 日志形态穷举(自由文本深度思考) ===

_MORPHOLOGY_SYSTEM = """你是板检日志格式分析专家。你的任务是**穷尽式**地分析某设备板检日志的每一种行形态与字段关系。
这是为后续「生成可复用抽取配置」做的深度思考,准确第一,不要节省篇幅。

请用自然语言(不要输出 JSON)写清楚以下内容:

1. 【日志行形态穷举】逐行说明样例里出现的每一种关键行,包括:
   - 每个字段(SN/MAC/ICCID/追溯码)在日志中**所有**不同的打印形态(前缀、分隔符、大小写、空白、多行/单行、发/收方向)。
   - 每种形态各给一个真实样例行,并指出「字段值」在行里的精确位置。
2. 【唯一标识字段】哪一个是设备的唯一主键(通常是与平台对账的追溯码),它在日志里出现几次、以什么形态为准。
3. 【派生关系】哪些字段可由其他字段推导(例如 MAC 的后 8 位/12 位来自追溯码),推导规则要写精确。
4. 【稳定字段】哪些字段物理上每台唯一且不变(SN、ICCID),它们的格式/长度/前缀。
5. 【头码/尾码】平台里的字段值(如 SN)在日志字段里是否被头码/尾码包裹(例如平台 SN=fa00...0050,日志 id=12345fa00...00506789)。若存在,说明如何剥离头尾码还原真值。
6. 【绑定原则】多串口并行测试时,SN/MAC/ICCID 如何与追溯码正确绑定到同一台设备(串口 + 时间窗口 + 派生校验)。
7. 【陷阱】已知坑:重复行、错误行、格式异常、字段缺失等。

请把你所有的观察都写出来,宁可啰嗦,不可遗漏。"""


def _analyze_morphology(
    client, device_code, description, sample_log, sample_csv, is_4g_hint
) -> str:
    user = _build_morphology_user(device_code, description, sample_log, sample_csv, is_4g_hint)
    last_text = ""
    for attempt in range(3):
        try:
            resp = client.complete(
                system_prompt=_MORPHOLOGY_SYSTEM, user_prompt=user, max_tokens=16384, temperature=0.0
            )
            text = (resp.text or "").strip()
            if text:
                return text
            last_text = resp.text
            user += "\n\n(上一轮返回了空内容,请直接输出你的完整分析文字。)"
        except Exception as e:
            logger.warning("形态穷举调用失败(第 %d 次): %s", attempt + 1, e)
            last_text = str(e)
    return "(形态分析返回空内容/失败,按通用规则处理): " + last_text[:500]


def _build_morphology_user(device_code, description, sample_log, sample_csv, is_4g_hint) -> str:
    parts = [
        f"设备代号: {device_code}",
        f"是否 4G 设备(用户指定): {is_4g_hint if is_4g_hint is not None else '未指定,请从描述/样例推断'}",
        "",
        "## 用户对板检规则的自然语言描述",
        description,
    ]
    if sample_log:
        parts += ["", "## 板检日志样例(节选)", "```text", sample_log[:_SAMPLE_LOG_MAX], "```"]
    else:
        parts += ["", "(未提供样例日志,请依据描述推断;不确定处明确写出「需人工确认」)"]
    if sample_csv:
        parts += ["", "## 平台 CSV 样例(节选,注意真实表头)", "```csv", sample_csv[:_SAMPLE_CSV_MAX], "```"]
    parts += [
        "",
        "请输出你的完整形态分析文字(自然语言,不要 JSON)。",
    ]
    return "\n".join(parts)


# === 阶段 A2: 派生关系实测(确定性 + LLM 兜底) ===

def _measure_derived_relations(
    sample_csv: str, sample_log: str, client, device_code: str
) -> Dict[str, Any]:
    """实测派生关系:平台 CSV(真值) ↔ 日志字段。

    优先用确定性方法:在样例 CSV 里数「蓝牙mac = 追溯码后 N 位」;再结合日志样例,
    让 LLM 推导 SN 头/尾码剥离规则与各字段权威形态。
    """
    result: Dict[str, Any] = {
        "mac_trace_suffix_len": None,
        "mac_derived_from_trace": False,
        "sn_strip_prefix": "",
        "sn_strip_suffix": "",
        "llm_used": False,
    }
    # 1) 确定性:从样例 CSV 数 mac ← 追溯码后 N 位
    if sample_csv.strip():
        n = _measure_mac_suffix_len(sample_csv)
        if n:
            result["mac_trace_suffix_len"] = n
            result["mac_derived_from_trace"] = True

    # 2) LLM 兜底:推导头尾码 + 权威形态(有样例日志时)
    if sample_log.strip():
        _report_derived_llm(client, device_code, sample_log, sample_csv, result)

    return result


def _measure_mac_suffix_len(sample_csv: str) -> Optional[int]:
    """在样例 CSV 里实测「mac = 追溯码后 N 位」。"""
    try:
        from .file_reader import _read_csv_text

        rows = _read_csv_text(sample_csv)
    except Exception:
        return None
    if not rows:
        return None
    # 找 mac 列与追溯码列(宽松:列名含 mac / 追溯)
    mac_key = next((k for k in rows[0] if "mac" in k.lower()), None)
    tc_key = next((k for k in rows[0] if "追溯" in k or "trace" in k.lower()), None)
    if not mac_key or not tc_key:
        return None
    for r in rows:
        tc = (r.get(tc_key) or "").strip().upper()
        mac = (r.get(mac_key) or "").strip().upper()
        if tc and mac and tc.endswith(mac) and 8 <= len(mac) <= 16:
            return len(mac)
        if tc and mac and mac.endswith(tc[-12:]) and len(tc) >= 12:
            return 12
    return None


def _report_derived_llm(client, device_code, sample_log, sample_csv, result: Dict[str, Any]) -> None:
    """让 LLM 从样例日志推导头/尾码剥离规则与权威形态(尽力而为,失败不影响主流程)。"""
    try:
        user = (
            f"设备代号: {device_code}\n\n"
            "## 任务:推导该设备日志里各字段的头码/尾码剥离规则\n"
            "平台是真值(如 SN 平台=fa00...0050, 日志 id=12345fa00...00506789 → 头码12345 尾码6789)。\n"
            "请基于下面的样例日志(和平台CSV),推导:\n"
            "1. SN 的头码/尾码(日志里 id 值如何剥离得到平台 SN);\n"
            "2. MAC 是否由追溯码派生(后几位相等);\n"
            "3. trace code 在日志里的权威形态(哪个最稳定唯一)。\n"
            "只输出 JSON: {\"sn_strip_prefix\":\"...\", \"sn_strip_suffix\":\"...\", "
            "\"mac_trace_suffix_len\":N, \"mac_derived_from_trace\":bool, "
            "\"trace_authoritative_forms\":[\"...\"]}\n"
        )
        if sample_log:
            user += f"\n## 板检日志样例\n```text\n{sample_log[:8000]}\n```\n"
        if sample_csv:
            user += f"\n## 平台CSV样例\n```csv\n{sample_csv[:4000]}\n```\n"
        from ..llm.parser import parse_json_response

        resp = client.complete(
            system_prompt="你是设备字段关系推导助手。只输出 JSON。",
            user_prompt=user,
            max_tokens=4096,
            temperature=0.0,
        )
        data = parse_json_response(resp.text)
        if isinstance(data, dict):
            for key in ("sn_strip_prefix", "sn_strip_suffix", "trace_authoritative_forms"):
                if data.get(key):
                    result[key] = data[key]
            if isinstance(data.get("mac_trace_suffix_len"), int) and data["mac_trace_suffix_len"] > 0:
                result["mac_trace_suffix_len"] = data["mac_trace_suffix_len"]
            if isinstance(data.get("mac_derived_from_trace"), bool):
                result["mac_derived_from_trace"] = data["mac_derived_from_trace"]
            result["llm_used"] = True
    except Exception:
        result["llm_used"] = False


# === 阶段 B: 字段规格(结构化) ===

_SPEC_SYSTEM = """你是板检数据核验 Agent 的设备字段规格设计专家。基于给定的「形态分析」,产出一份结构化的字段抽取规格,严格输出 JSON。

字段约定(通用):
- sn: 设备序列号(= 平台里的「设备编号」,物理稳定,每台唯一)
- mac: MAC 地址(常与追溯码存在派生关系)
- iccid: 4G SIM 卡号(19~20 位,通常以 8986 开头;非 4G 设备置空)
- trace_code: 追溯码(与平台对账的**唯一主键**)

正则必须是合法的 Python/JSON 字符串(注意转义 \\d \\s 等)。

⚠️ 捕获组约定(直接决定抽取器能否拿到「值」而非「标签」):
- 每个字段的 regex 或 context_anchor 里,**必须有一个用括号包住「字段值本身」的捕获组**。
- context_anchor 若只是定位前缀(后面没有括号),抽取器会拿到整个前缀而不是值——绝对禁止。
- 把「值」用括号包起来,前缀/上下文不要用括号。

⚠️ 头尾码剥离约定(平台值为标准,日志字段可能被头码/尾码包裹):
- 若日志字段值 = 头码 + 平台真值 + 尾码,请优先在正则里用括号只捕获「平台真值」部分(剥掉头尾码);
- 若无法确定剥离边界,则捕获整个字段值,并在 head_tail_rules 里说明,由比对层用「包含匹配」兜底。
"""


def _build_spec(
    client, device_code, description, sample_log, sample_csv, is_4g_hint, morphology: str,
    derived: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    user = _build_spec_user(device_code, description, sample_log, sample_csv, is_4g_hint, morphology, derived)
    data = _call_json(client, _SPEC_SYSTEM, user, label="字段规格", max_tokens=16384)
    if not isinstance(data, dict):
        raise DeviceConfigError("字段规格返回格式错误")
    # 把实测的派生关系合并进规格(LLM 生成时参考)
    if derived:
        data.setdefault("derived_measurements", derived)
    return data


def _build_spec_user(device_code, description, sample_log, sample_csv, is_4g_hint, morphology: str, derived=None) -> str:
    parts = [
        f"设备代号: {device_code}",
        f"是否 4G 设备(用户指定): {is_4g_hint if is_4g_hint is not None else '未指定,请从描述/样例推断'}",
        "",
        "## 用户对板检规则的自然语言描述",
        description,
        "",
        "## 形态分析(深度思考结果,依据它归纳规格)",
        morphology[:_SAMPLE_LOG_MAX],
    ]
    if derived:
        parts += ["", "## 派生关系实测(平台为准,可执行结论)", json.dumps(derived, ensure_ascii=False, indent=2)]
    if sample_log:
        parts += ["", "## 板检日志样例(节选)", "```text", sample_log[:_SAMPLE_LOG_MAX], "```"]
    if sample_csv:
        parts += [
            "",
            "## 平台 CSV 样例(节选)",
            "```csv",
            sample_csv[:_SAMPLE_CSV_MAX],
            "```",
            "(platform_csv_columns 必须逐字抄上面 CSV 的真实表头,严禁臆造成 SN/MAC/ICCID 等通用名)",
        ]
    else:
        parts += ["", "(未提供样例 CSV,platform_csv_columns 按通用名猜测;生成后自行核对真实表头)"]
    parts += [
        "",
        "请输出如下结构的 JSON(只输出 JSON):",
        json.dumps(
            {
                "display_name": "产品显示名",
                "is_4g": False,
                "unique_key": "trace_code",
                "derived_fields": ["mac 由 trace_code 的哪一段推导,写精确规则"],
                "stable_fields": ["sn", "iccid"],
                "fields": {
                    "sn": {"regex": "SN正则(尽量剥头尾码)", "context_anchor": "锚点正则", "description": "说明", "example": "示例值", "must_be_present": True},
                    "mac": {"regex": "MAC正则", "context_anchor": "锚点正则", "description": "说明", "example": "示例值", "must_be_present": True},
                    "iccid": {"regex": "ICCID正则(非4G写空)", "context_anchor": "锚点正则", "description": "说明", "example": "示例值", "must_be_present": False},
                    "trace_code": {"regex": "追溯码正则", "context_anchor": "锚点正则", "description": "说明", "example": "示例值", "must_be_present": True},
                },
                "field_constraints": {
                    "trace_code_last_8_chars_equal_mac": True,
                    "sn_format": "SN 完整格式正则",
                    "iccid_starts_with": "8986",
                    "iccid_length": [19, 20],
                },
                "platform_csv_columns": {"sn": "平台CSV中SN的列名(必须抄真实表头,禁止臆造)", "mac": "平台CSV中MAC的列名(必须抄真实表头)", "iccid": "平台CSV中ICCID的列名(必须抄真实表头)", "trace_code": "平台CSV中追溯码的列名(必须抄真实表头)"},
                "binding": {"window_seconds": 60, "description": "如何绑定同一设备的多个关键行"},
                "head_tail_rules": {"sn": "SN头尾码剥离规则(无则写 none)", "mac": "none", "iccid": "none", "trace_code": "none"},
                "keep_patterns": ["应保留的正则行(含关键字段)"],
                "drop_patterns": ["应剔除的正则行(无关行)"],
                "traps": ["该设备已知陷阱/注意事项"],
            },
            ensure_ascii=False,
            indent=2,
        ),
    ]
    return "\n".join(parts)


# === 调用② 生成配置 ===

_GENERATE_SYSTEM = """你是板检数据核验 Agent 的设备配置生成器。请根据给定的结构分析结果,
参照 A02FG 模板,产出与 A02FG 同构的四件套配置。只输出一个 JSON 对象,包含四个键:
- device_json: 对应 device.json
- filter_rules_json: 对应 filter_rules.json
- extraction_rules_json: 对应 extraction_rules.json
- prompts_md: 对应 prompts.md 的完整 markdown 字符串

所有正则必须是合法 JSON 字符串(正确转义反斜杠)。不要遗漏任何字段。

⚠️ field_extractors 捕获组约定(否则抽取器会拿到「COM口/字段名」而不是值):
- 每个字段的「regex」和「context_anchor」中,必须有一个用括号 `(...)` 把「字段值本身」包起来。
- 推荐写法:regex 与 context_anchor 都带捕获组,且两者指向同一个值,例如:
  regex:          `COM\\d+:收mac:([0-9A-F]{12})`
  context_anchor: `COM\\d+:收mac:([0-9A-F]{12})`
- 若某字段是「定位前缀 + 值」,请把「值」用括号包住,前缀不要用括号。严禁出现「前缀里没有捕获组」的锚点。
- 非 4G 设备的 iccid:regex 与 context_anchor 均写空字符串 ""。
"""


def _generate_config(
    client,
    device_code,
    description,
    spec,
    morphology,
    sample_log,
    sample_csv,
    feedback: str = "",
) -> Dict[str, Any]:
    template = _load_template_files()
    example = _load_example_files()
    user = _build_generate_user(
        device_code, description, spec, morphology, template, example, sample_log, feedback
    )
    data = _call_json(client, _GENERATE_SYSTEM, user, label="生成配置", max_tokens=32768)
    if not isinstance(data, dict):
        raise DeviceConfigError("配置生成返回格式错误")
    for key in ("device_json", "filter_rules_json", "extraction_rules_json", "prompts_md"):
        if key not in data:
            raise DeviceConfigError(f"生成的配置缺少字段: {key}")
    return data


def _build_generate_user(
    device_code, description, spec, morphology, template, example, sample_log, feedback: str = ""
) -> str:
    parts = [
        f"设备代号: {device_code}",
        f"产品描述: {description}",
        "",
        "## 字段规格(依据)",
        json.dumps(spec, ensure_ascii=False, indent=2),
        "",
        "## 形态分析(深度思考结果,供写正则与绑定规则参考)",
        morphology[:_SAMPLE_LOG_MAX],
        "",
        "## 配置模板(结构性参照)",
        "```json",
        json.dumps(template, ensure_ascii=False, indent=2),
        "```",
        "",
        "## A02FG 完整示例(可作为写法的具体参照)",
        "```json",
        json.dumps(example, ensure_ascii=False, indent=2),
        "```",
        "",
        "请产出四件套配置 JSON。要求:",
        "1. device_json 里的 device_code 必须是给定的代号,display_name 用规格里的名称",
        "2. field_extractors 与 field_definitions 的字段正则保持一致",
        "3. 严格遵守「捕获组约定」:每个字段的 regex 或 context_anchor 必须有包住字段值的括号",
        "4. 优先按 head_tail_rules 剥离头尾码,正则只捕获「平台真值」部分",
        "5. prompts_md 用中文写清楚每个字段的格式与日志行模式、绑定原则、已知陷阱",
        "6. 若 is_4g=false,iccid 相关字段置空但仍保留结构",
        "只输出 JSON,格式: {\"device_json\":{...},\"filter_rules_json\":{...},\"extraction_rules_json\":{...},\"prompts_md\":\"...\"}",
    ]
    if feedback:
        parts += ["", "## ⚠️ 上一轮输出的结构问题(务必修正)", feedback]
    return "\n".join(parts)


# === 调用③ 修正 ===

_FIX_SYSTEM = """你是正则修正专家。给定设备配置与样例日志,自检后发现某些字段正则无法在样例中命中,
请修正这些正则,使它们能正确匹配样例,同时保持不误匹配。只输出修正后的四件套 JSON(与生成时相同结构)。"""


def _fix_regexes(
    client,
    device_code,
    config,
    sample_log,
    preview,
    morphology: str = "",
    spec: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    issues = preview.get("issues") or []
    if not issues:
        return None
    spec_json = json.dumps(spec or {}, ensure_ascii=False, indent=2)
    user = (
        f"设备代号: {device_code}\n\n"
        f"## 字段规格(依据)\n```json\n{spec_json}\n```\n\n"
        f"## 形态分析(参考)\n{morphology[:_SAMPLE_LOG_MAX]}\n\n"
        f"## 当前配置\n```json\n{json.dumps(config, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 自检发现的问题\n"
        + "\n".join(f"- {i}" for i in issues)
        + f"\n\n## 板检日志样例\n```text\n{sample_log[:_SAMPLE_LOG_MAX]}\n```\n\n"
        "请修正正则并输出四件套 JSON(结构同生成阶段):"
        " {\"device_json\":{...},\"filter_rules_json\":{...},\"extraction_rules_json\":{...},\"prompts_md\":\"...\"}"
    )
    data = _call_json(client, _FIX_SYSTEM, user, label="正则修正", max_tokens=32768)
    if not isinstance(data, dict) or "extraction_rules_json" not in data:
        return None
    return data


# === LLM 调用与 JSON 解析 ===

def _call_json(
    client,
    system: str,
    user: str,
    label: str = "调用",
    max_tokens: int = 4096,
    retries: int = 3,
) -> Dict[str, Any]:
    """调用 LLM 并解析 JSON。

    LLM 可能瞬时返回空内容 / 非 JSON / 被 max_tokens 截断的输出,
    因此解析失败时带反馈重试,而非一次性报错。
    """
    last_text = ""
    for attempt in range(retries):
        resp = client.complete(
            system_prompt=system, user_prompt=user, max_tokens=max_tokens, temperature=0.0
        )
        data = parse_json_response(resp.text)
        if data is not None:
            return data

        last_text = resp.text
        stop_reason = (resp.raw or {}).get("stop_reason")
        if stop_reason == "max_tokens":
            # 输出被截断:调高 token 上限并提示精简
            max_tokens = min(max_tokens * 2, 32768)
            user += "\n\n(注意:上一轮你的输出被截断了,请输出更紧凑、完整的 JSON,严禁省略任何字段。)"
            logger.warning("LLM %s 输出被截断,已把 max_tokens 提到 %d 重试", label, max_tokens)
        elif not resp.text.strip():
            user += "\n\n(上一轮返回了空内容,请严格只输出一个 JSON 对象。)"
            logger.warning("LLM %s 返回空内容,重试 %d/%d", label, attempt + 1, retries)
        else:
            user += "\n\n(上一轮返回的不是合法 JSON,请严格只输出一个 JSON 对象,不要任何解释文字。)"
            logger.warning("LLM %s 返回无法解析 JSON,重试 %d/%d", label, attempt + 1, retries)

    logger.warning("LLM %s 返回无法解析的 JSON(重试 %d 次后仍失败):\n%s", label, retries, last_text[:800])
    raise DeviceConfigError(
        f"LLM {label} 连续 {retries} 次返回无法解析的 JSON,已放弃。"
        "可能是模型过载或返回被截断,请稍后重试,或在「LLM 设置」里换一个模型。"
    )


# === 写入与自检 ===

def _unwrap(obj: Any, keys: tuple) -> Dict[str, Any]:
    """解开 LLM 可能多包的一层,如 {"extraction_rules": {...}} → {...}。

    LLM 常把「extraction_rules.json 的内容」写成 {"extraction_rules": {...}},
    因为顶层输出键就叫 extraction_rules_json。这里检测并展开。
    """
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and isinstance(obj[k], dict) and obj[k]:
                return obj[k]
    return obj if isinstance(obj, dict) else {}


def _normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """归一化生成的四件套结构,消除 LLM 的多层嵌套偏差。"""
    config = dict(config)
    config["device_json"] = _unwrap(config.get("device_json"), ("device", "device_json"))
    config["filter_rules_json"] = _unwrap(
        config.get("filter_rules_json"), ("filter_rules", "filter_rules_json")
    )
    config["extraction_rules_json"] = _unwrap(
        config.get("extraction_rules_json"), ("extraction_rules", "extraction_rules_json")
    )
    return config


def _validate_config_structure(config: Dict[str, Any]) -> List[str]:
    """校验归一化后的配置是否具备可用的最小结构,返回问题列表(空=通过)。"""
    problems: List[str] = []
    extraction = config.get("extraction_rules_json") or {}
    if not isinstance(extraction, dict):
        return ["extraction_rules_json 不是对象"]

    fe = extraction.get("field_extractors")
    if not isinstance(fe, dict) or not fe:
        return ["extraction_rules_json 顶层缺少 field_extractors"]

    for field in ("sn", "mac", "trace_code"):
        cfg = fe.get(field)
        if not isinstance(cfg, dict):
            problems.append(f"field_extractors 缺少字段 {field}")
            continue
        if not (cfg.get("regex") or cfg.get("context_anchor")):
            problems.append(f"字段 {field} 的 regex 与 context_anchor 均为空")
    return problems


def _write_device_files(device_code: str, config: Dict[str, Any], rules_dir: Optional[Path] = None) -> Path:
    rules_dir = rules_dir or (get_user_devices_dir() / device_code)
    rules_dir.mkdir(parents=True, exist_ok=True)

    # 归一化(解开可能的多层嵌套)后再写入
    device_json = _unwrap(config.get("device_json"), ("device", "device_json"))
    filter_json = _unwrap(config.get("filter_rules_json"), ("filter_rules", "filter_rules_json"))
    extraction_json = _unwrap(
        config.get("extraction_rules_json"), ("extraction_rules", "extraction_rules_json")
    )
    prompts_md = config.get("prompts_md") or ""

    # 强制保证 device_code 与代号一致
    device_json = dict(device_json)
    device_json["device_code"] = device_code

    (rules_dir / "device.json").write_text(
        json.dumps(device_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (rules_dir / "filter_rules.json").write_text(
        json.dumps(filter_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (rules_dir / "extraction_rules.json").write_text(
        json.dumps(extraction_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (rules_dir / "prompts.md").write_text(prompts_md, encoding="utf-8")
    return rules_dir


def _validate_and_preview(
    device_code: str, rules_dir: Path, sample_log: str, sample_csv: str = ""
) -> Dict[str, Any]:
    """确定性硬质量自检: 加载规则(正则编译) + 样例试抽 + 去重率 + 唯一性 + 平台列名。"""
    issues: List[str] = []
    needs_fix = False

    # 1. 编译校验
    try:
        rules = DeviceRules.load(device_code)
    except Exception as e:
        return {
            "compiled": False,
            "issues": [f"规则加载失败(正则可能不合法): {e}"],
            "needs_fix": True,
            "records": 0,
            "field_coverage": {},
        }

    # 2. 平台列名校验(有样例 CSV 时)
    if sample_csv.strip():
        col_issues = _check_sample_csv_columns(sample_csv, rules)
        issues.extend(col_issues)
        if col_issues:
            needs_fix = True

    # 3. 样例试抽 + 数据质量
    if not sample_log.strip():
        return {
            "compiled": True,
            "issues": issues,
            "needs_fix": needs_fix,
            "records": 0,
            "field_coverage": {},
            "note": "未提供样例日志,仅校验正则可编译与平台列名",
        }

    from .preprocessor import preprocess_text_log
    from .extractor import extract_from_text_log
    from .quality import dedupe_and_validate

    sample_lines = sample_log.splitlines()
    kept = preprocess_text_log(sample_lines, rules)
    records = extract_from_text_log(kept, rules)
    raw_count = len(records)
    records, quality = dedupe_and_validate(records, rules)

    field_names = ["sn", "mac", "trace_code"] + (["iccid"] if rules.is_4g else [])
    coverage: Dict[str, float] = {}
    for fname in field_names:
        if not records:
            coverage[fname] = 0.0
            continue
        got = sum(1 for r in records if getattr(r, fname))
        coverage[fname] = round(got / len(records) * 100, 1)

    # 4. 硬质量门槛(不达标才需要修正)
    for fname in field_names:
        if coverage.get(fname, 0) < 80:
            needs_fix = True
            issues.append(f"字段 {fname} 在样例中抽全率仅 {coverage[fname]}% (<80%),正则可能不准确")

    if raw_count == 0:
        needs_fix = True
        issues.append("样例日志中未抽取到任何设备记录,锚点正则可能不匹配")

    if quality.get("conflicts"):
        needs_fix = True
        issues.append(f"样例抽取存在 {quality['conflicts']} 处字段唯一性冲突(一对多),疑似绑定错误")

    if quality.get("truncated_iccid"):
        needs_fix = True
        issues.append(f"样例抽取存在 {quality['truncated_iccid']} 条 ICCID 长度异常")

    # 4.5 值合法性门槛:抽出的字段值不得像「标签/串口/字段名」(治 COM 口字段名回归)
    _add_value_validity_issues(records, issues, needs_fix)

    return {
        "compiled": True,
        "issues": issues,
        "needs_fix": needs_fix,
        "records": len(records),
        "raw_records": raw_count,
        "deduped": quality.get("deduped", 0),
        "field_coverage": coverage,
        "kept_lines": len(kept),
    }


def _add_value_validity_issues(records: List[Any], issues: List[str], needs_fix: bool) -> None:
    """值合法性门槛:抽出的字段值若含 COM/冒号/过长(像标签不像值),判失败回炉。"""
    import re as _re
    for r in records:
        for field in ("sn", "mac", "iccid", "trace_code"):
            val = getattr(r, field)
            if not val:
                continue
            v = str(val).strip()
            bad = False
            if _re.search(r"COM\d+", v, _re.IGNORECASE):
                bad = True
            elif ":" in v:
                bad = True
            elif field == "iccid" and len(v) > 20:
                bad = True
            elif field in ("sn", "mac", "trace_code") and len(v) > 60:
                bad = True
            if bad:
                needs_fix = True
                issues.append(
                    f"字段 {field} 抽出的值像「标签/串口」而非真值:{v[:40]!r},正则可能捕获了前缀"
                )
                break


def _check_sample_csv_columns(sample_csv: str, rules: DeviceRules) -> List[str]:
    """用样例 CSV 表头校验 platform_csv_columns 是否可解析。"""
    from .file_reader import _read_csv_text

    try:
        rows = _read_csv_text(sample_csv)
    except Exception:
        return []
    if not rows:
        return []
    actual = [k.strip() for k in rows[0].keys() if k]
    if not actual:
        return []
    cols = rules.platform_csv_columns or {}
    issues: List[str] = []
    for field in ("sn", "mac", "trace_code"):
        mapped = (cols.get(field) or "").strip()
        if mapped and mapped not in actual:
            issues.append(
                f"平台列名 {field}→{mapped!r} 在样例 CSV 表头({', '.join(actual)})中不存在"
            )
    if rules.is_4g:
        mapped = (cols.get("iccid") or "").strip()
        if mapped and mapped not in actual:
            issues.append(
                f"平台列名 iccid→{mapped!r} 在样例 CSV 表头({', '.join(actual)})中不存在"
            )
    return issues


# === 模板与示例加载 ===

def _load_template_files() -> Dict[str, Any]:
    from ..config import AGENT_ROOT

    tpl_dir = AGENT_ROOT / "rules" / "_template"
    out: Dict[str, Any] = {}
    for name in ("device.json", "filter_rules.json", "extraction_rules.json"):
        p = tpl_dir / name
        if p.is_file():
            try:
                out[name.replace(".json", "")] = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                out[name.replace(".json", "")] = {}
    return out


def _load_example_files() -> Dict[str, Any]:
    example_dir = DEVICES_DIR / "A02FG"
    out: Dict[str, Any] = {}
    if not example_dir.is_dir():
        return out
    for name in ("device.json", "filter_rules.json", "extraction_rules.json"):
        p = example_dir / name
        if p.is_file():
            try:
                out[name.replace(".json", "")] = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
    prompts_p = example_dir / "prompts.md"
    if prompts_p.is_file():
        out["prompts_md"] = prompts_p.read_text(encoding="utf-8")
    return out
