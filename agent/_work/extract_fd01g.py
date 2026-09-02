# -*- coding: utf-8 -*-
"""
FD01G 追溯码 -> SN 映射抽取

策略:
- 5/21 旧批次:日志打 "收 :id:" (32 hex SN),可直接正则抽取 SN
- 5/25+ 新批次:用 568B0816 命令写入 SN,日志不再打 :id:;
  但 MAC 派生自追溯码末 12 位,所以无法仅从日志反推 SN。
- 通用策略:无论新旧批次,都尝试从日志抽出 (trace, MAC) 对;
  旧批次还抽 (trace, SN) 对。

输入:
- Info-2026-05-{21,25,26,27,30}.txt
输出:
- D:/000AI_project_achieve/agent_project_plan/agent/_work/fd01g_sessions.json
  含 sessions: [{date, time, com, trace, mac, sn}, ...]
"""
import re
import json
from pathlib import Path

BASE = Path(r"D:/000AI_project_achieve/agent_project_plan/Device_doc/FD01G/FD01G板检问题核验")

# 追溯码权威形态:回复网关SN:F233QSHI5... (变体4位 + 9位hex)
TRACE_RE = re.compile(r"回复网关SN\s*:\s*(F233QSHI5[A-Z0-9]{4}[0-9A-Fa-f]{9})")
# 收 :id: 32 hex SN(5/21 旧批次)
SN_RE = re.compile(r"收\s*:id\s*:\s*([0-9A-Fa-f]{32})")
# MAC 行(大写 MAC,12 hex)
MAC_RE = re.compile(r"(?<![0-9A-Fa-f])MAC\s*:\s*([0-9A-Fa-f]{12})(?![0-9A-Fa-f])")
# ICCID 行
ICCID_RE = re.compile(r"4G\s+CCID\s*:\s*(8986[0-9A-Fa-f]{16})")
# 时间戳前缀(行首 HH:MM:SS mmm)
TIME_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+(\d{3})")
# COM 端口
COM_RE = re.compile(r"COM\d+")
# FD01G 配置头
FD01G_RE = re.compile(r'"ProductModel":"FD01G"')

def parse_file(path: Path, date: str):
    sessions = []
    cur = None  # dict
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            tmatch = TIME_RE.match(line)
            time_str = tmatch.group(1) if tmatch else None
            ms = tmatch.group(2) if tmatch else None

            # 检测 FD01G 板检配置头
            if FD01G_RE.search(line):
                # 标记新会话起始
                if cur is not None:
                    sessions.append(cur)
                cur = {
                    "date": date,
                    "start_time": time_str,
                    "ms": ms,
                    "trace": None,
                    "mac": None,
                    "sn": None,
                    "iccid": None,
                    "com": None,
                    "raw_header": line.strip(),
                }
                continue

            if cur is None:
                continue

            m = TRACE_RE.search(line)
            if m and cur["trace"] is None:
                cur["trace"] = m.group(1).upper()
                # 提取 COM 端口
                com_match = COM_RE.search(line)
                if com_match:
                    cur["com"] = com_match.group(0)
                if not cur.get("trace_time"):
                    cur["trace_time"] = time_str
                continue

            m = SN_RE.search(line)
            if m and cur["sn"] is None:
                cur["sn"] = m.group(1).upper()
                continue

            m = MAC_RE.search(line)
            if m and cur["mac"] is None:
                cur["mac"] = m.group(1).upper()
                continue

            m = ICCID_RE.search(line)
            if m and cur["iccid"] is None:
                cur["iccid"] = m.group(1).upper()
                continue

        if cur is not None:
            sessions.append(cur)

    return sessions


def main():
    files = [
        ("2026-05-21", BASE / "Info-2026-05-21.txt"),
        ("2026-05-25", BASE / "Info-2026-05-25.txt"),
        ("2026-05-26", BASE / "Info-2026-05-26.txt"),
        ("2026-05-27", BASE / "Info-2026-05-27.txt"),
        ("2026-05-30", BASE / "Info-2026-05-30.txt"),
    ]
    all_sessions = []
    for date, path in files:
        if not path.exists():
            print(f"SKIP: {path}")
            continue
        s = parse_file(path, date)
        print(f"{date}: {len(s)} sessions")
        all_sessions.extend(s)

    # 输出
    out = BASE.parent.parent / "agent" / "_work" / "fd01g_sessions.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_sessions, f, ensure_ascii=False, indent=2)

    # 汇总
    with_trace = [s for s in all_sessions if s["trace"]]
    with_sn = [s for s in all_sessions if s["sn"]]
    with_mac = [s for s in all_sessions if s["mac"]]
    print(f"Total FD01G sessions: {len(all_sessions)}")
    print(f"  with trace: {len(with_trace)}")
    print(f"  with SN:    {len(with_sn)}")
    print(f"  with MAC:   {len(with_mac)}")
    # trace→SN
    trace_to_sn = {}
    for s in all_sessions:
        if s["trace"] and s["sn"]:
            trace_to_sn[s["trace"]] = s["sn"]
    print(f"  unique trace→SN: {len(trace_to_sn)}")

    # 显示前 3 条
    for s in all_sessions[:3]:
        print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()