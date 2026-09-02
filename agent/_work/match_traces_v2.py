# -*- coding: utf-8 -*-
"""
重跑 FD01G 240 追溯码 -> SN 全量映射

改进:
- SN 抽取支持两种形态:
  ① 收 :id:<SN>          (旧式,5/21)
  ② 收:560D0410<SN>18    (新式 568B0816 协议应答,5/25+)
- 关联策略:每个 "回复网关SN:..." 行的 30 秒时间窗内,找最近的
  "收:560D0410<SN>18" 或 "收 :id:<SN>"(任一即可),得到 (trace, SN) 对。
- 单台设备在一次会话内可能出现多次 SN 应答,默认保留首次。
"""
import re
import json
import csv
from pathlib import Path
from collections import defaultdict

BASE = Path(r"D:/000AI_project_achieve/agent_project_plan/Device_doc/FD01G/FD01G板检问题核验")
TRACES_FILE = BASE / "追溯码240.md"
CSV_FILE = BASE / "FD01G板检记录2605210530.csv"
OUT_DIR = Path(r"D:/000AI_project_achieve/agent_project_plan/agent/_work")

# --- 正则 ---
TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\s+(\d{3})")
COM_RE = re.compile(r"COM(\d+)")
FD01G_RE = re.compile(r'"ProductModel":"FD01G"')
# trace 出现行(回复网关SN:)
TRACE_LINE_RE = re.compile(r"回复网关SN\s*:\s*(F233QSHI5[A-Z0-9]{4}[0-9A-Fa-f]{9})")
# SN 形态 ①
SN_OLD_RE = re.compile(r"收\s*:id\s*:\s*([0-9A-Fa-f]{32})")
# SN 形态 ②
SN_NEW_RE = re.compile(r"收\s*:560D0410([0-9A-Fa-f]{32})")
# SN 形态③(授权请求包 560E0410 携带 SN)
SN_AUTH_RE = re.compile(r"收\s*:560E0410([0-9A-Fa-f]{32})")


def hms_to_sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_log(path: Path, date: str):
    """返回 trace->SN 映射(基于 trace 回复时间窗内的最近 SN)。"""
    trace_sn = {}  # trace -> (sn, source_line_no, trace_time)
    sn_seen = {}  # sn -> 第一次出现的 sec(用于排除重复)
    events = []    # 全部事件:[(sec, kind, payload, lineno)]

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for ln, line in enumerate(f, 1):
            tm = TIME_RE.match(line)
            if not tm:
                continue
            sec = hms_to_sec(*tm.groups())
            # trace
            m = TRACE_LINE_RE.search(line)
            if m:
                trace = m.group(1).upper()
                events.append((sec, "trace", (trace, ln), ln))
                continue
            # SN 形态①
            m = SN_OLD_RE.search(line)
            if m:
                sn = m.group(1).upper()
                events.append((sec, "sn", (sn, "old", ln), ln))
                continue
            # SN 形态②
            m = SN_NEW_RE.search(line)
            if m:
                sn = m.group(1).upper()
                events.append((sec, "sn", (sn, "new", ln), ln))
                continue
            # SN 形态③(560E0410 授权包携带 SN)
            m = SN_AUTH_RE.search(line)
            if m:
                sn = m.group(1).upper()
                events.append((sec, "sn", (sn, "auth", ln), ln))
                continue

    # 关联:每个 trace 事件向后 30 秒找最近 SN 事件
    # (设备可能在写入成功后立即回包;实际日志中一般 < 5s)
    WINDOW = 30
    for i, (sec_t, kind_t, pay_t, ln_t) in enumerate(events):
        if kind_t != "trace":
            continue
        trace, _ = pay_t
        best = None
        for j in range(i + 1, len(events)):
            sec_s, kind_s, pay_s, ln_s = events[j]
            if sec_s - sec_t > WINDOW:
                break
            if kind_s == "sn":
                best = pay_s
                break  # 最近的即可
        if best is not None:
            sn, fmt, ln_s = best
            if trace not in trace_sn:
                trace_sn[trace] = {
                    "sn": sn,
                    "format": fmt,
                    "sn_lineno": ln_s,
                    "trace_lineno": ln_t,
                    "date": date,
                }

    # 也支持"先 SN 后 trace"的情形(部分批次):用同一 SN 关联最近的 trace
    # 但 FD01G 协议里 trace 回复总在 SN 应答之前,先 SN 后 trace 的很少,暂不处理
    return trace_sn


def main():
    files = [
        ("2026-05-21", BASE / "Info-2026-05-21.txt"),
        ("2026-05-25", BASE / "Info-2026-05-25.txt"),
        ("2026-05-26", BASE / "Info-2026-05-26.txt"),
        ("2026-05-27", BASE / "Info-2026-05-27.txt"),
        ("2026-05-30", BASE / "Info-2026-05-30.txt"),
    ]
    # 1. 日志抽取 trace→SN
    all_trace_sn = {}
    all_traces_only = set()
    for date, path in files:
        m = parse_log(path, date)
        for t, info in m.items():
            all_traces_only.add(t)
            if t not in all_trace_sn:
                all_trace_sn[t] = info
        print(f"{date}: trace→SN 配对 {len(m)} 个,日志累计 trace {len(all_traces_only)} 个")
    print(f"---日志合计: trace→SN 配对 {len(all_trace_sn)}, 独立 trace {len(all_traces_only)}---")

    # 2. 读取 240 个追溯码
    traces_240 = []
    with open(TRACES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t:
                traces_240.append(t.upper())

    # 3. 匹配
    rows = []
    sn_hits = 0
    log_only_hits = 0
    miss_log = 0
    for i, t in enumerate(traces_240, 1):
        info = all_trace_sn.get(t)
        if info:
            sn_hits += 1
            rows.append({
                "idx": i,
                "trace": t,
                "sn": info["sn"],
                "sn_format": info["format"],
                "source": f"log:{info['date']}",
                "sn_lineno": info["sn_lineno"],
            })
        else:
            miss_log += 1
            rows.append({
                "idx": i,
                "trace": t,
                "sn": None,
                "sn_format": None,
                "source": None,
                "sn_lineno": None,
            })

    print(f"\n=== 240 追溯码 vs 日志 ===")
    print(f"日志可定位 SN: {sn_hits} / 240")
    print(f"日志无 SN: {miss_log} / 240")

    # 4. 输出
    out_json = OUT_DIR / "trace_to_sn_log_only.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\n写入: {out_json}")


if __name__ == "__main__":
    main()