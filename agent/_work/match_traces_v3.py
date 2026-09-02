# -*- coding: utf-8 -*-
"""
FD01G trace → SN 正确配对 v3

核心修正:
- SN 配对必须按 COM 端口隔离
- 同一 COM 内,trace 回复时间向后找本 COM 的下一条 SN 事件(任意形态)

(原 v2 用全局时间窗,导致并发板检时 3 个 COM 的 SN 错位分配)
"""
import re
import json
from pathlib import Path

BASE = Path(r"D:/000AI_project_achieve/agent_project_plan/Device_doc/FD01G/FD01G板检问题核验")
TRACES_FILE = BASE / "追溯码240.md"
OUT_DIR = Path(r"D:/000AI_project_achieve/agent_project_plan/agent/_work")

TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\s+(\d{3})")
COM_RE = re.compile(r"(COM\d+)")
TRACE_RE = re.compile(r"回复网关SN\s*:\s*(F233QSHI5[A-Z0-9]{4}[0-9A-Fa-f]{9})")
SN_OLD_RE = re.compile(r"收\s*:id\s*:\s*([0-9A-Fa-f]{32})")
SN_NEW_RE = re.compile(r"收\s*:560D0410([0-9A-Fa-f]{32})")
SN_AUTH_RE = re.compile(r"收\s*:560E0410([0-9A-Fa-f]{32})")


def to_sec(t):
    return int(t.group(1)) * 3600 + int(t.group(2)) * 60 + int(t.group(3)) + int(t.group(4)) / 1000


def parse_per_com(path: Path, date: str):
    """按 COM 拆事件,再在每 COM 内做 trace→SN 配对。"""
    events_by_com = {}
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for ln, line in enumerate(f, 1):
            tm = TIME_RE.match(line)
            if not tm:
                continue
            sec = to_sec(tm)
            cm = COM_RE.search(line)
            if not cm:
                continue
            com = cm.group(1)

            mt = TRACE_RE.search(line)
            if mt:
                events_by_com.setdefault(com, []).append(
                    (sec, "trace", mt.group(1).upper(), ln)
                )
                continue
            for pat, fmt in [(SN_OLD_RE, "old"), (SN_NEW_RE, "new"), (SN_AUTH_RE, "auth")]:
                ms = pat.search(line)
                if ms:
                    events_by_com.setdefault(com, []).append(
                        (sec, "sn", ms.group(1).upper(), ln)
                    )
                    break

    # 每 COM 中配对:trace 事件向后 60s 内找下一条 SN
    trace_sn = {}
    for com, evs in events_by_com.items():
        for i, (sec_t, kind_t, t, _) in enumerate(evs):
            if kind_t != "trace":
                continue
            if t in trace_sn:
                continue
            for j in range(i + 1, len(evs)):
                sec_s, kind_s, val_s, ln_s = evs[j]
                if sec_s - sec_t > 60:
                    break
                if kind_s == "sn":
                    trace_sn[t] = {
                        "sn": val_s,
                        "format": "mixed",
                        "sn_lineno": ln_s,
                        "trace_lineno": evs[i][3],
                        "date": date,
                        "com": com,
                    }
                    break
    return trace_sn


def main():
    files = [
        ("2026-05-21", BASE / "Info-2026-05-21.txt"),
        ("2026-05-25", BASE / "Info-2026-05-25.txt"),
        ("2026-05-26", BASE / "Info-2026-05-26.txt"),
        ("2026-05-27", BASE / "Info-2026-05-27.txt"),
        ("2026-05-30", BASE / "Info-2026-05-30.txt"),
    ]
    all_trace_sn = {}
    com_summary = {}
    for date, path in files:
        m = parse_per_com(path, date)
        com_summary[date] = {}
        for com, evs in parse_per_com.__wrapped__ if False else []:
            pass
        for t, info in m.items():
            if t not in all_trace_sn:
                all_trace_sn[t] = info
        print(f"{date}: COM 内 trace→SN 配对 {len(m)} 个")

    print(f"\n---日志合计:独立 trace {len(all_trace_sn)}")

    # 检测:同一 trace 对应多 SN? (不可能了,com 内 1 对 1)
    # 检测:同一 SN 被多 trace 引用?
    sn_used = {}
    for t, info in all_trace_sn.items():
        sn_used.setdefault(info["sn"], []).append((t, info["date"], info["com"]))
    multi_sn = {s: v for s, v in sn_used.items() if len(v) > 1}
    print(f"---一个 SN 对应多 trace 的: {len(multi_sn)} (应 = 0)")
    for s, refs in list(multi_sn.items())[:5]:
        print(f"  SN={s}")
        for r in refs:
            print(f"    trace={r[0]}  date={r[1]}  com={r[2]}")

    # 读 240 个追溯码
    traces_240 = []
    with open(TRACES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t:
                traces_240.append(t.upper())

    rows = []
    sn_hits = 0
    for i, t in enumerate(traces_240, 1):
        info = all_trace_sn.get(t)
        if info:
            sn_hits += 1
            rows.append({
                "idx": i,
                "trace": t,
                "sn": info["sn"],
                "sn_format": info["format"],
                "source": f"log:{info['date']}:{info['com']}",
                "sn_lineno": info["sn_lineno"],
            })
        else:
            rows.append({
                "idx": i,
                "trace": t,
                "sn": None,
                "sn_format": None,
                "source": None,
                "sn_lineno": None,
            })

    print(f"\n=== 240 追溯码 ===")
    print(f"日志可定位 SN: {sn_hits} / 240")
    print(f"日志无 SN: {240 - sn_hits} / 240")

    out = OUT_DIR / "trace_to_sn_v3.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\n写入: {out}")


if __name__ == "__main__":
    main()