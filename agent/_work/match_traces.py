# -*- coding: utf-8 -*-
"""
读取 CSV 与日志,把 240 个追溯码映射到 SN。

数据源:
- CSV 平台导出:1495 行 FD01G;含 追溯码→A系列uuid(32 hex SN)
- 日志:5/21~5/30 共 29 次 FD01G 板检会话

策略:
- 主匹配:CSV(权威)
- 补强:用日志中 trace→SN 验证;CSV 缺失时回退日志
"""
import csv
import json
import re
from pathlib import Path

BASE = Path(r"D:/000AI_project_achieve/agent_project_plan/Device_doc/FD01G/FD01G板检问题核验")
TRACES_FILE = BASE / "追溯码240.md"
CSV_FILE = BASE / "FD01G板检记录2605210530.csv"
SESSIONS_JSON = BASE.parent.parent / "agent" / "_work" / "fd01g_sessions.json"


def load_traces():
    with open(TRACES_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_csv():
    """CSV → {trace: {sn, mac, iccid, created_at, key}}"""
    out = {}
    with open(CSV_FILE, "r", encoding="utf-8-sig") as f:
        rdr = csv.reader(f)
        hdr = next(rdr)
        idx = {h: i for i, h in enumerate(hdr)}
        for row in rdr:
            trace = row[idx["追溯码"]].strip().upper()
            if not trace.startswith("F233QSHI5"):
                continue
            sn = row[idx["A系列uuid"]].strip().upper()
            mac = row[idx["蓝牙mac"]].strip().upper()
            iccid = row[idx["4G信息"]].strip().upper()
            out[trace] = {
                "key": row[idx["主键"]].strip(),
                "sn": sn,
                "mac": mac,
                "iccid": iccid,
                "created_at": row[idx["创建时间"]].strip(),
            }
    return out


def load_log_sessions():
    with open(SESSIONS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for s in data:
        if s.get("trace"):
            out[s["trace"].upper()] = s
    return out


def main():
    traces = load_traces()
    csv_map = load_csv()
    log_map = load_log_sessions()

    print(f"输入追溯码: {len(traces)}")
    print(f"CSV 追溯码覆盖: {len(csv_map)}")
    print(f"日志追溯码覆盖: {len(log_map)}")

    rows = []
    missing_csv = 0
    missing_sn_csv = 0
    filled_from_log = 0
    for i, t in enumerate(traces, 1):
        t_up = t.upper()
        csv_row = csv_map.get(t_up)
        log_row = log_map.get(t_up)

        sn = None
        mac = None
        iccid = None
        csv_key = None
        csv_created = None
        csv_has_no_sn = False
        if csv_row:
            csv_key = csv_row["key"]
            csv_created = csv_row["created_at"]
            sn = csv_row["sn"] or None
            mac = csv_row["mac"] or None
            iccid = csv_row["iccid"] or None
            if csv_row and not csv_row["sn"]:
                missing_sn_csv += 1
        else:
            missing_csv += 1

        # 若 CSV 无 SN,回退日志
        if not sn and log_row and log_row.get("sn"):
            sn = log_row["sn"]
            filled_from_log += 1

        log_session = None
        if log_row:
            log_session = {
                "date": log_row["date"],
                "trace_time": log_row.get("trace_time") or log_row.get("start_time"),
                "com": log_row.get("com"),
                "mac_in_log": log_row.get("mac"),
                "sn_in_log": log_row.get("sn"),
            }

        rows.append({
            "idx": i,
            "trace": t_up,
            "csv_key": csv_key,
            "created_at": csv_created,
            "sn": sn,
            "mac": mac,
            "iccid": iccid,
            "csv_hit": bool(csv_row),
            "csv_has_no_sn": csv_has_no_sn,
            "log_hit": bool(log_row),
            "log_session": log_session,
        })

    print(f"\n=== 匹配统计 ===")
    print(f"CSV 缺失(此 240 个 trace 在 CSV 完全找不到): {missing_csv}")
    print(f"CSV 命中但 A系列uuid(写入SN)为空: {missing_sn_csv}")
    print(f"日志补强 SN 数: {filled_from_log}")
    sn_count = sum(1 for r in rows if r["sn"])
    print(f"最终可定位 SN: {sn_count}/{len(traces)}")

    out = Path(r"D:/000AI_project_achieve/agent_project_plan/agent/_work/trace_to_sn.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\n结果写入: {out}")

    # 把命中但无 SN 的 + 缺失的列出
    print("\n=== CSV 缺失的追溯码(前 20 个) ===")
    n = 0
    for r in rows:
        if not r["csv_hit"]:
            print(f"  #{r['idx']:3d} {r['trace']}")
            n += 1
            if n >= 20:
                print("  ...")
                break
    print(f"  合计: {missing_csv}")

    print("\n=== CSV 命中但 SN 空的(前 20 个) ===")
    n = 0
    for r in rows:
        if r["csv_hit"] and not r["sn"]:
            print(f"  #{r['idx']:3d} {r['trace']}  csv_key={r['csv_key']}  log_hit={r['log_hit']}  log_sn={r['log_session']['sn_in_log'] if r['log_session'] else None}")
            n += 1
            if n >= 20:
                print("  ...")
                break
    print(f"  合计: {missing_sn_csv}")


if __name__ == "__main__":
    main()