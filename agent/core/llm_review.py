"""LLM 复核层:独立抽取 + 交叉验证 + 比对结果复核 + 不确定记录定向复核。

设计原则(防止 LLM 引入新错误):
1. LLM 永远不静默覆盖确定性的正则结果 —— 只有当 LLM 的值通过硬性字段约束
   (MAC 后8位==追溯码后8位、SN 格式、ICCID 格式)时才采纳,否则保留正则值。
2. 所有分歧都被记录到 notes / disagreements,便于报告与人工复核。
3. 未配置 API Key 或显式禁用时优雅跳过,不影响确定性流程。

该模块作为"第二双眼睛"对确定性管线做交叉验证:
- 阶段一(independent_extraction): LLM 独立抽取整份日志 → 与正则对账 → 修正错绑字段
- 阶段一·五(conflict_review): 质量层报 1:1 冲突/低置信度的记录 → 定向喂锚点窗口,
  LLM 判定哪条才是该追溯码的真值,经硬约束仲裁后才采纳(精准打击错绑,不扫整份日志)
- 阶段二(comparison_review): LLM 复核比对出的不一致记录 → 判定真实问题还是误报
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .extractor import DeviceRecord
from .rule_loader import DeviceRules
from ..llm.client import create_default_client
from ..llm.parser import parse_json_response
from ..llm.prompts import SYSTEM_ROLE_BASE, DEVICE_RECORD_SCHEMA

logger = logging.getLogger(__name__)


# === 阶段一:独立抽取 + 交叉验证 ===

def extract_records_with_llm(
    text_lines: List[str],
    rules: DeviceRules,
    client,
    chunk_lines: int = 200,
) -> List[DeviceRecord]:
    """LLM 独立抽取整份日志的设备记录(分块),失败时返回空列表。"""
    if not text_lines:
        return []
    chunks = [text_lines[i:i + chunk_lines] for i in range(0, len(text_lines), chunk_lines)]
    records: List[DeviceRecord] = []
    success_chunks = 0
    zero_chunks = 0
    fail_chunks = 0
    for ci, chunk in enumerate(chunks):
        try:
            data = _call_extraction(chunk, rules, client)
            recs = _parse_device_records(data)
            records.extend(recs)
            if recs:
                success_chunks += 1
            else:
                zero_chunks += 1
            logger.info("LLM 独立抽取 chunk %d/%d → %d 条", ci + 1, len(chunks), len(recs))
        except Exception as e:
            fail_chunks += 1
            logger.warning("LLM 独立抽取 chunk %d 失败: %s", ci + 1, e)
    if chunks and success_chunks == 0:
        logger.warning(
            "LLM 独立抽取空转:共 %d chunk,成功解析 0 个(0条 %d,失败 %d),复核未生效",
            len(chunks), zero_chunks, fail_chunks,
        )
    return records


def _call_extraction(chunk: List[str], rules: DeviceRules, client) -> Optional[Dict[str, Any]]:
    system = _build_system(rules)
    user = _build_extraction_user(chunk, rules)
    resp = client.complete(
        system_prompt=system,
        user_prompt=user,
        max_tokens=4096,
        temperature=0.0,
    )
    return parse_json_response(resp.text)


def _build_system(rules: DeviceRules) -> str:
    parts = [SYSTEM_ROLE_BASE, "", rules.get_4g_indicator()]
    if rules.prompts_md:
        parts.append("")
        parts.append("## 设备专属规则")
        parts.append(_strip_code_blocks(rules.prompts_md)[:4000])
    return "\n".join(parts)


def _strip_code_blocks(md: str) -> str:
    lines: List[str] = []
    in_code_block = False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            lines.append(line)
    return "\n".join(lines).strip()


def _build_extraction_user(chunk: List[str], rules: DeviceRules) -> str:
    field_names = ["sn", "mac", "trace_code"] + (["iccid"] if rules.is_4g else [])
    text = "\n".join(chunk)
    return (
        "请从下面的板检日志片段中,独立抽取每一台设备的身份字段。\n"
        "以『回复追溯码』行为锚点,按『同串口 + 时间最近』原则把 SN/MAC/ICCID 绑定到同一台设备。\n"
        f"需要抽取的字段: {', '.join(field_names)}。\n"
        f"输出 JSON Schema:\n{json.dumps(DEVICE_RECORD_SCHEMA, ensure_ascii=False, indent=2)}\n\n"
        "## 板检日志片段\n```text\n"
        f"{text[:30000]}\n```\n\n"
        "只输出 JSON,不要输出任何解释。字段无法确定时填 null,不要编造值。"
    )


def _parse_device_records(data: Any) -> List[DeviceRecord]:
    if not isinstance(data, dict):
        return []
    out: List[DeviceRecord] = []
    for item in data.get("device_records") or []:
        if not isinstance(item, dict):
            continue
        tc = _norm(item.get("trace_code"))
        if not tc:
            continue
        out.append(
            DeviceRecord(
                sn=_norm(item.get("sn")),
                mac=_norm(item.get("mac")),
                iccid=_norm(item.get("iccid")),
                trace_code=tc,
                com_port=item.get("com_port"),
                trace_response_time=item.get("trace_response_time"),
                confidence=(item.get("confidence") or "medium"),
                extraction_method="llm",
                notes=item.get("notes") or "",
            )
        )
    return out


def _norm(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    return s.upper()


def reconcile_records(
    regex_records: List[DeviceRecord],
    llm_records: List[DeviceRecord],
    rules: DeviceRules,
) -> List[Dict[str, Any]]:
    """LLM 与正则结果交叉验证。

    对每个字段:两者一致则跳过;不一致时用硬性约束仲裁,
    LLM 值仅在其通过约束、且正则值未通过约束时才覆盖正则值。
    返回分歧列表(含是否被采纳),供报告与日志使用。
    """
    llm_by_trace: Dict[str, List[DeviceRecord]] = {}
    for lr in llm_records:
        if lr.trace_code:
            llm_by_trace.setdefault(lr.trace_code, []).append(lr)

    disagreements: List[Dict[str, Any]] = []
    for rr in regex_records:
        if not rr.trace_code:
            continue
        candidates = llm_by_trace.get(rr.trace_code)
        if not candidates:
            continue
        lr = candidates[0]
        for field in ("sn", "mac", "iccid", "trace_code"):
            if field == "iccid" and not rules.is_4g:
                continue
            regex_val = getattr(rr, field)
            llm_val = getattr(lr, field)
            if _same(regex_val, llm_val):
                continue

            adopted = _arbitrate(rr, field, regex_val, llm_val, rules)
            changed = bool(adopted is not None and _norm(adopted) != _norm(regex_val))
            if changed:
                setattr(rr, field, adopted)
                rr.extraction_method = "llm_reconciled"
                note = f"{field} 经 LLM 交叉验证修正"
                rr.notes = (rr.notes + "; " + note).strip("; ")

            disagreements.append(
                {
                    "trace_code": rr.trace_code,
                    "field": field,
                    "regex": regex_val,
                    "llm": llm_val,
                    "adopted": adopted if changed else regex_val,
                    "changed": changed,
                    "note": "" if changed else "分歧未采纳 LLM 值,保留正则",
                }
            )

    if disagreements:
        logger.info("LLM 交叉验证:发现 %d 处分歧,修正 %d 处",
                    len(disagreements), sum(1 for d in disagreements if d["changed"]))
    return disagreements


def _arbitrate(
    rr: DeviceRecord,
    field: str,
    regex_val: Optional[str],
    llm_val: Optional[str],
    rules: DeviceRules,
) -> Optional[str]:
    """裁决采纳哪个值。返回应采纳的值(可能等于 regex_val,表示保留正则)。"""
    regex_val = _norm(regex_val)
    llm_val = _norm(llm_val)
    if not llm_val:
        return regex_val  # LLM 无值 → 保留正则
    if not regex_val:
        # 正则缺失、LLM 有值 → 必须通过硬性约束才采纳
        return llm_val if _passes_constraint(field, llm_val, rr.trace_code, rules) else None

    tc = rr.trace_code or ""
    if field == "mac":
        # 追溯码后8位 == MAC后8位 是确定性判据
        regex_ok = _mac_matches_trace(regex_val, tc)
        llm_ok = _mac_matches_trace(llm_val, tc)
        if llm_ok and not regex_ok:
            return llm_val
        return regex_val

    # sn / iccid / trace_code: 用格式约束
    regex_ok = _passes_constraint(field, regex_val, tc, rules)
    llm_ok = _passes_constraint(field, llm_val, tc, rules)
    if llm_ok and not regex_ok:
        return llm_val
    return regex_val


def _passes_constraint(field: str, val: Optional[str], trace_code: Optional[str], rules: DeviceRules) -> bool:
    if not val:
        return False
    fc = rules.field_constraints or {}
    if field == "sn":
        fmt = fc.get("sn_format")
        if fmt:
            return bool(re.fullmatch(fmt, val, re.IGNORECASE))
        return True
    if field == "iccid":
        starts = fc.get("iccid_starts_with")
        if starts and not val.upper().startswith(starts.upper()):
            return False
        lo, hi = fc.get("iccid_length", [0, 999])
        return lo <= len(val) <= hi
    if field == "trace_code":
        return True  # 追溯码是锚点,不轻易改
    return True


def _mac_matches_trace(mac: Optional[str], trace_code: Optional[str]) -> bool:
    if not mac or not trace_code or len(mac) < 8 or len(trace_code) < 8:
        return False
    return mac[-8:].upper() == trace_code[-8:].upper()


def _same(a: Optional[str], b: Optional[str]) -> bool:
    return _norm(a) == _norm(b)


# === 阶段二:比对结果复核 ===

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["reviews"],
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["trace_code", "verdict", "reason"],
                "properties": {
                    "trace_code": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["confirmed", "binding_artifact", "data_scope", "false_positive"],
                    },
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


def review_comparison(
    rows: List[Any],
    kept_lines: List[str],
    rules: DeviceRules,
    client,
    max_records: int = 100,
) -> Dict[str, Dict[str, str]]:
    """LLM 复核比对出的不一致记录,返回 {trace_code: {verdict, reason}}。"""
    inconsistent = [r for r in rows if r.overall_status in ("MISMATCH", "RAW_ONLY", "PLATFORM_ONLY")]
    if not inconsistent:
        return {}
    target = inconsistent[:max_records]

    system = _build_system(rules)
    user = _build_review_user(target, kept_lines, rules)
    try:
        resp = client.complete(
            system_prompt=system,
            user_prompt=user,
            max_tokens=4096,
            temperature=0.0,
        )
        data = parse_json_response(resp.text)
        return _parse_reviews(data)
    except Exception as e:
        logger.warning("LLM 比对复核失败: %s", e)
        return {}


def _build_review_user(rows: List[Any], kept_lines: List[str], rules: DeviceRules) -> str:
    lines: List[str] = []
    lines.append(
        "请复核以下『板检数据核验』结果,判断每条不一致记录是真实问题还是流程产生的误报。\n"
        "判定类别:\n"
        "- confirmed: 真实问题(例如平台 SN 存的是内部 UUID 而非真实 SN)\n"
        "- binding_artifact: 疑似字段绑定错误(日志中相邻设备字段被错绑)\n"
        "- data_scope: 数据范围不匹配(如平台有、日志无,可能是不同日期/批次)\n"
        "- false_positive: 其他误报\n"
    )
    for r in rows:
        raw = r.raw_record
        plat = r.platform_record or {}
        lines.append(f"### 追溯码 {r.trace_code or '-'} (状态={r.overall_status})")
        lines.append(f"原始: SN={raw.sn if raw else '-'} MAC={raw.mac if raw else '-'} "
                     f"ICCID={raw.iccid if raw else '-'}")
        lines.append(f"平台: SN={plat.get('sn') or '-'} MAC={plat.get('mac') or '-'} "
                     f"ICCID={plat.get('iccid') or '-'}")
        ctx = _grab_context(kept_lines, raw.source_line if raw else None, radius=20)
        lines.append("日志上下文:")
        lines.append("```text")
        lines.append(ctx or "(无原始日志上下文)")
        lines.append("```")
    lines.append(f"\n输出 JSON Schema:\n{json.dumps(REVIEW_SCHEMA, ensure_ascii=False, indent=2)}\n\n只输出 JSON。")
    return "\n".join(lines)


def _grab_context(lines: List[str], source_line: Optional[int], radius: int) -> str:
    if not lines:
        return ""
    if not source_line:
        return "\n".join(lines[: radius * 2])
    start = max(0, source_line - 1 - radius)
    end = min(len(lines), source_line - 1 + radius + 1)
    return "\n".join(lines[start:end])


def _parse_reviews(data: Any) -> Dict[str, Dict[str, str]]:
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for item in data.get("reviews") or []:
        if not isinstance(item, dict):
            continue
        tc = _norm(item.get("trace_code"))
        if not tc:
            continue
        out[tc] = {
            "verdict": item.get("verdict") or "false_positive",
            "reason": item.get("reason") or "",
        }
    return out


def _apply_reviews(rows: List[Any], reviews: Dict[str, Dict[str, str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in rows:
        if not r.trace_code:
            continue
        rev = reviews.get(r.trace_code.upper())
        if not rev:
            continue
        verdict = rev["verdict"]
        counts[verdict] = counts.get(verdict, 0) + 1
        label = {
            "confirmed": "真实问题",
            "binding_artifact": "疑似绑定错误",
            "data_scope": "疑似批次不同",
            "false_positive": "疑似误报",
        }.get(verdict, verdict)
        r.notes = (r.notes + f" [LLM复核:{label}]").strip()
    return counts


# === 编排入口 ===

def llm_reconcile_extraction(
    rules: DeviceRules,
    kept_lines: List[str],
    regex_records: List[DeviceRecord],
    enabled: Optional[bool] = None,
) -> tuple[List[DeviceRecord], Dict[str, Any]]:
    """阶段一:LLM 独立抽取 + 交叉验证(比对之前调用)。

    Args:
        enabled: 显式开关。True/False 强制开/关;None 回退到设备配置
                 llm_verification.enabled && independent_extraction。

    Returns:
        (records, meta): records 可能被 LLM 修正;meta 记录调用与分歧统计。
    """
    cfg = rules.extraction_rules.get("llm_verification") or {}
    if enabled is None:
        enabled = bool(cfg.get("enabled")) and bool(cfg.get("independent_extraction", True))
    if not enabled:
        return regex_records, {"enabled": False}
    if os.environ.get("AGENT_DISABLE_LLM"):
        return regex_records, {"enabled": False, "skipped": "AGENT_DISABLE_LLM"}

    client = create_default_client()
    if client is None:
        logger.info("LLM 交叉验证跳过:未配置 API Key")
        return regex_records, {"enabled": True, "skipped": "no_api_key"}

    chunk_lines = int(cfg.get("chunk_lines", 200))
    llm_records = extract_records_with_llm(kept_lines, rules, client, chunk_lines)
    disagreements = reconcile_records(regex_records, llm_records, rules)

    meta: Dict[str, Any] = {
        "enabled": True,
        "stage": "independent_extraction",
        "calls": _chunk_count(kept_lines, chunk_lines),
        "llm_extracted": len(llm_records),
        "disagreements": len(disagreements),
        "corrected": sum(1 for d in disagreements if d["changed"]),
        "extraction_effective": len(llm_records) > 0,
    }
    return regex_records, meta


def llm_review_comparison(
    rules: DeviceRules,
    kept_lines: List[str],
    rows: List[Any],
    enabled: Optional[bool] = None,
) -> tuple[List[Any], Dict[str, Any]]:
    """阶段二:LLM 复核比对结果(比对之后、报告之前调用)。

    Args:
        enabled: 显式开关。True/False 强制开/关;None 回退到设备配置
                 llm_verification.enabled && comparison_review。
    """
    cfg = rules.extraction_rules.get("llm_verification") or {}
    if enabled is None:
        enabled = bool(cfg.get("enabled")) and bool(cfg.get("comparison_review", True))
    if not enabled:
        return rows, {"enabled": False}
    if os.environ.get("AGENT_DISABLE_LLM"):
        return rows, {"enabled": False, "skipped": "AGENT_DISABLE_LLM"}

    client = create_default_client()
    if client is None:
        logger.info("LLM 比对复核跳过:未配置 API Key")
        return rows, {"enabled": True, "skipped": "no_api_key"}

    max_records = int(cfg.get("max_review_records", 100))
    reviews = review_comparison(rows, kept_lines, rules, client, max_records)
    counts = _apply_reviews(rows, reviews)

    inconsistent = sum(1 for r in rows if r.overall_status in ("MISMATCH", "RAW_ONLY", "PLATFORM_ONLY"))
    effective = bool(reviews) or not inconsistent

    return rows, {
        "enabled": True,
        "stage": "comparison_review",
        "calls": 1 if reviews else 0,
        "reviewed": len(reviews),
        "verdicts": counts,
        "review_effective": effective,
    }


# === 阶段一·五:不确定记录定向复核(质量层之后、比对之前) ===

CONFLICT_REVIEW_SCHEMA = {
    "type": "object",
    "required": ["assignments"],
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["trace_code", "sn", "mac", "iccid"],
                "properties": {
                    "trace_code": {"type": "string"},
                    "sn": {"type": "string"},
                    "mac": {"type": "string"},
                    "iccid": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
            },
        },
        # L2 受控 agentic:模型可在此主动请求「定向重读」(代码闸门放行,受预算约束)
        "need_reexamine": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["trace_code", "reason"],
                "properties": {
                    "trace_code": {"type": "string"},
                    "reason": {"type": "string", "description": "为何上下文不足,需要重读"},
                    "window": {"type": "string", "description": "想重看的串口/时间范围,如 COM3 16:50-16:52"},
                },
            },
            "description": "可选:上下文不足时请求定向重读",
        },
    },
}


def llm_review_conflicts(
    rules: DeviceRules,
    kept_lines: List[str],
    records: List[DeviceRecord],
    enabled: Optional[bool] = None,
) -> tuple[List[DeviceRecord], Dict[str, Any]]:
    """对「低置信度 / 1:1 冲突」记录做定向 LLM 复核。

    只喂每个嫌疑记录「锚点 ± 窗口」的日志片段,让 LLM 判定该追溯码的 SN/MAC/ICCID
    哪条才是真值;判定结果必须过 `_passes_constraint` 硬约束才采纳,绝不静默覆盖。

    Args:
        enabled: 显式开关。True/False 强制开/关;None 回退到设备配置
                 llm_verification.enabled && conflict_review。
    """
    cfg = rules.extraction_rules.get("llm_verification") or {}
    if enabled is None:
        enabled = bool(cfg.get("enabled")) and bool(cfg.get("conflict_review", True))
    if not enabled:
        return records, {"enabled": False}
    if os.environ.get("AGENT_DISABLE_LLM"):
        return records, {"enabled": False, "skipped": "AGENT_DISABLE_LLM"}

    # 只复核确定性层已经标记为「低置信度」的记录(1:1 冲突 / 派生不一致 / 长度异常)
    suspects = [r for r in records if (r.confidence or "") == "low" and r.trace_code]
    if not suspects:
        return records, {"enabled": True, "suspects": 0, "reviewed": 0, "corrected": 0}

    client = create_default_client()
    if client is None:
        logger.info("LLM 不确定记录复核跳过:未配置 API Key")
        return records, {"enabled": True, "skipped": "no_api_key"}

    # 推理模型把 token 全耗在 thinking 上会空转(见 docs/debugging/05):
    # 只对「独立抽取」阶段依赖整份日志扫描的模型做空转拦截;
    # 定向复核是聚焦小窗口、单条调用,仍需把 max_tokens 留给推理模型思考,
    # 因此把 max_tokens 放大到 4096 并让 prompts 显式要求"只输出 JSON"。
    max_records = int(cfg.get("max_conflict_records", 20))
    window = int(cfg.get("conflict_window_seconds", 120))
    radius = int(cfg.get("conflict_context_radius", 40))
    radius_bump = int(cfg.get("conflict_context_radius_bump", 2))
    max_tokens = int(cfg.get("conflict_max_tokens", 4096))
    # L2 受控 agentic 预算:模型自决「定向重读」的放行上限(默认 5 条总重读、每条至多 1 次)
    max_reexamine_total = int(cfg.get("max_reexamine_total", 5))
    max_reexamine_per_record = int(cfg.get("max_reexamine_per_record", 1))
    reviewed = 0
    corrected = 0
    zero_responses = 0
    re_examined = 0
    re_examine_granted = 0
    re_examine_denied = 0
    reexamine_budget = max_reexamine_total
    details: List[Dict[str, Any]] = []

    for rec in suspects[:max_records]:
        reviewed += 1
        context = _conflict_context(kept_lines, rec, window, radius)
        assignment, reexamine = _call_conflict_review(rules, client, rec, context, max_tokens)

        # L2:模型自决「定向重读」——模型请求更宽上下文,代码闸门放行(受预算/每记录上限约束)
        if reexamine and reexamine_budget > 0 and re_examined < max_reexamine_per_record:
            re_examined += 1
            reexamine_budget -= 1
            re_examine_granted += 1
            wider = _conflict_context(
                kept_lines, rec, window, max(radius, int(radius * radius_bump))
            )
            if wider != context:
                assignment2, _ = _call_conflict_review(rules, client, rec, wider, max_tokens)
                if assignment2 is not None:
                    assignment = assignment2
                else:
                    zero_responses += 1
        elif reexamine:
            re_examine_denied += 1

        if assignment is None:
            if not reexamine:
                zero_responses += 1
            continue

        fields = assignment
        changed_fields: List[str] = []
        for fname in ("sn", "mac", "iccid"):
            val = _norm(fields.get(fname))
            if not val:
                continue
            current = getattr(rec, fname)
            if _same(current, val):
                continue
            # 硬约束仲裁:LLM 值不合法就丢弃
            if not _passes_constraint(fname, val, rec.trace_code, rules):
                logger.info("LLM 复核字段 %s 未过约束,丢弃: %s", fname, val)
                continue
            setattr(rec, fname, val)
            changed_fields.append(fname)
        if changed_fields:
            corrected += 1
            rec.extraction_method = "llm_conflict_reviewed"
            rec.confidence = fields.get("confidence") or "medium"
            rec.notes = (rec.notes + " [LLM定向复核:已修正 " + ",".join(changed_fields) + "]").strip()
            details.append({
                "trace_code": rec.trace_code,
                "corrected": changed_fields,
                "reason": fields.get("reason") or "",
            })
            logger.info("LLM 定向复核修正 %s: %s", rec.trace_code, changed_fields)

    meta: Dict[str, Any] = {
        "enabled": True,
        "stage": "conflict_review",
        "suspects": len(suspects),
        "reviewed": reviewed,
        "corrected": corrected,
        "zero_responses": zero_responses,
        "re_examined": re_examined,
        "re_examine_granted": re_examine_granted,
        "re_examine_denied": re_examine_denied,
        "details": details,
    }
    # 空转探针:全部 0 响应时明确告警(推理模型 thinking 耗尽 token)
    if reviewed > 0 and zero_responses == reviewed:
        meta["warning"] = "全部记录 LLM 复核 0 响应,疑似推理模型空转(token 被 thinking 耗尽)"
        logger.warning("LLM 不确定记录复核空转: %d/%d 0 响应", zero_responses, reviewed)
    return records, meta


def _conflict_context(
    kept_lines: List[str], rec: DeviceRecord, window_seconds: int, radius: int
) -> str:
    """取嫌疑记录锚点附近(同串口 + 时间窗口)的日志片段,定向喂给 LLM。"""
    if not kept_lines:
        return ""
    # 优先按时间窗口过滤同串口行;若无法解析时间,退化为 source_line ± radius
    import re as _re
    com = (rec.com_port or "").strip()
    anchor_ts = rec.trace_response_time or ""
    timed: List[str] = []
    if com and anchor_ts:
        try:
            h, m, s = (int(x) for x in anchor_ts.split(":"))
            anchor_sec = h * 3600 + m * 60 + s
            for line in kept_lines:
                if f"COM{com}" not in line:
                    continue
                m2 = _re.match(r"^(\d{2}):(\d{2}):(\d{2})", line)
                if not m2:
                    continue
                sec = int(m2.group(1)) * 3600 + int(m2.group(2)) * 60 + int(m2.group(3))
                if abs(sec - anchor_sec) <= window_seconds:
                    timed.append(line)
        except (ValueError, AttributeError):
            pass
    if timed:
        return "\n".join(timed)
    # 退化:source_line ± radius
    if rec.source_line:
        start = max(0, rec.source_line - 1 - radius)
        end = min(len(kept_lines), rec.source_line - 1 + radius + 1)
        return "\n".join(kept_lines[start:end])
    return "\n".join(kept_lines[: radius * 2])


def _call_conflict_review(
    rules: DeviceRules, client, rec: DeviceRecord, context: str, max_tokens: int
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """调用 LLM 复核单条嫌疑记录,返回 (assignment, reexamine)。

    assignment: 与本追溯码匹配的 assignment 项(可能为 None — 解析失败/未命中)。
    reexamine:   模型的 need_reexamine 请求项(若存在并指向本记录);未请求则 None。

    L2 受控 agentic:模型可在 response 中通过顶层 `need_reexamine` 主动请求「上下文不足,
    想重看更宽窗口」,由调用方决定是否放行(预算内才发起第二次定向调用)。
    """
    system = _build_system(rules)
    field_names = ["sn", "mac", "trace_code"] + (["iccid"] if rules.is_4g else [])
    user = (
        "以下『板检数据核验』记录被判定为「低置信度 / 疑似字段错绑」"
        "(同串口同码多次重试,或 SN/MAC/ICCID 一值对应多个追溯码)。\n"
        "请根据日志上下文,判断该追溯码对应的『真实 SN / MAC / ICCID』"
        "——以『回复追溯码:』行为锚点,取同串口、时间最近的字段值。\n\n"
        f"当前(可能错绑的)记录: 追溯码={rec.trace_code}, 串口=COM{rec.com_port or '?'}, "
        f"SN={rec.sn or '-'}, MAC={rec.mac or '-'}, ICCID={rec.iccid or '-'}。\n"
        f"需要确认的字段: {', '.join(field_names)}。\n\n"
        "## 日志上下文(锚点附近)\n```text\n"
        f"{context[:12000]}\n```\n\n"
        f"输出 JSON Schema:\n{json.dumps(CONFLICT_REVIEW_SCHEMA, ensure_ascii=False, indent=2)}\n\n"
        "只输出 JSON,不要输出任何分析过程。\n"
        "若当前上下文不足,可在顶层 need_reexamine 数组里说明想重看的窗口(串口/时间范围)与原因;\n"
        "若上下文足够,直接给出 assignments(无法确定字段填当前值,confidence 填 low)。"
    )
    try:
        resp = client.complete(
            system_prompt=system,
            user_prompt=user,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        data = parse_json_response(resp.text)
        if not isinstance(data, dict):
            return None, None
        # 解析 assignment(与本追溯码匹配的那一项)
        items = data.get("assignments") or []
        assignment: Optional[Dict[str, Any]] = None
        for item in items:
            if isinstance(item, dict) and _norm(item.get("trace_code")) == _norm(rec.trace_code):
                assignment = item
                break
        if assignment is None and items:
            assignment = items[0] if isinstance(items[0], dict) else None
        # 解析 reexamine(指向本记录的请求)
        reexamine: Optional[Dict[str, Any]] = None
        for r in (data.get("need_reexamine") or []):
            if isinstance(r, dict) and _norm(r.get("trace_code")) == _norm(rec.trace_code):
                reexamine = r
                break
        return assignment, reexamine
    except Exception as e:
        logger.warning("LLM 不确定记录复核失败 %s: %s", rec.trace_code, e)
    return None, None


def _chunk_count(lines: List[str], chunk_lines: int) -> int:
    if not lines:
        return 0
    return int(math.ceil(len(lines) / max(1, chunk_lines)))
