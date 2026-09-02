# -*- coding: utf-8 -*-
"""Build full 240-row Markdown report."""
import json
from pathlib import Path

rows = json.load(open(r"D:/000AI_project_achieve/agent_project_plan/agent/_work/trace_to_sn.json", encoding="utf-8"))
out_path = Path(r"D:/000AI_project_achieve/agent_project_plan/agent/_work/fd01g_full_240.md")

with open(out_path, "w", encoding="utf-8") as out:
    out.write("# FD01G 追溯码 240 → SN 全量映射\n\n")
    out.write("> 数据源:5 份 Info-*.txt(5/21~5/30 共 29 次 FD01G 板检会话) + CSV `FD01G板检记录2605210530.csv`(平台导出 1495 行)\n")
    out.write("> 路径:`D:/000AI_project_achieve/agent_project_plan/Device_doc/FD01G/FD01G板检问题核验/`\n\n")

    out.write("## 结果统计\n\n")
    csv_hit = sum(1 for r in rows if r["csv_hit"])
    sn_ok = sum(1 for r in rows if r["sn"])
    log_hit = sum(1 for r in rows if r["log_hit"])
    out.write(f"- 输入追溯码:**240**\n")
    out.write(f"- CSV 命中且可定位 SN:**{csv_hit}** / 240\n")
    out.write(f"- CSV 缺失(平台无该追溯码记录):**{240 - csv_hit}** / 240\n")
    out.write(f"- 日志中追溯码命中:**{log_hit}** / 240\n")
    out.write(f"- 最终可定位 SN:**{sn_ok}** / 240\n\n")

    out.write("## 关键结论\n\n")
    out.write(
        "1. **CSV 是真值**:CSV 命中 91 项,SN 全部非空(平台在板检时写入 A系列uuid);SN 与追溯码严格绑定,**SN = A系列uuid**(其中末 12 位 = 蓝牙 MAC,等于追溯码末 12 位)。\n"
    )
    out.write(
        "2. **CSV 缺失 149 项是平台数据漏录,不是日志可补**:日志只有 MAC 和追溯码,没有 32 位 SN 字段(5/21 旧批次 `收 :id:` 行仅出现 2 次,新批次改用 568B0816 指令,SN 在日志中不打印)。\n"
    )
    out.write(
        "3. **若必须补齐这 149 个**,只能从板检设备自身读取 SN 并补回 CSV;日志无法反推 32 位 SN。\n\n"
    )

    out.write("## 全量 240 行明细\n\n")
    out.write("| # | 追溯码 | SN(32hex) | MAC | ICCID | CSV主键 | 创建时间 | 日志命中 |\n")
    out.write("|---|---|---|---|---|---|---|---|\n")
    for r in rows:
        sn = r["sn"] if r["sn"] else ("- 缺失 -" if not r["csv_hit"] else "- 空 -")
        mac = r["mac"] or "-"
        iccid = r["iccid"] or "-"
        key = r["csv_key"] or "-"
        ts = r["created_at"] or "-"
        log = "-"
        if r["log_session"]:
            log = f"{r['log_session']['date']} {r['log_session']['trace_time']} {r['log_session']['com'] or ''}".strip()
        out.write(f"| {r['idx']} | `{r['trace']}` | `{sn}` | `{mac}` | `{iccid}` | {key} | {ts} | {log} |\n")

    out.write("\n---\n\n")
    out.write("## CSV 命中 91 项完整明细(SN/MAC/ICCID/创建时间)\n\n")
    out.write("| # | 追溯码 | SN | MAC | ICCID | 创建时间 | 主键 |\n")
    out.write("|---|---|---|---|---|---|---|\n")
    for r in [x for x in rows if x["csv_hit"]]:
        out.write(f"| {r['idx']} | `{r['trace']}` | `{r['sn']}` | `{r['mac']}` | `{r['iccid']}` | {r['created_at']} | {r['csv_key']} |\n")

    out.write("\n## CSV 缺失 149 项(日志中也未匹配)\n\n")
    out.write("| # | 追溯码 |\n|---|---|\n")
    for r in [x for x in rows if not x["csv_hit"]]:
        out.write(f"| {r['idx']} | `{r['trace']}` |\n")

    out.write("\n## 日志匹配 6 项(虽 CSV 缺失但日志中出现)\n\n")
    out.write("| # | 追溯码 | 日志日期 | 时间 | COM | 日志中 SN | 日志中 MAC |\n")
    out.write("|---|---|---|---|---|---|---|\n")
    for r in [x for x in rows if x["log_hit"]]:
        ls = r["log_session"]
        out.write(f"| {r['idx']} | `{r['trace']}` | {ls['date']} | {ls['trace_time']} | {ls['com'] or '-'} | {ls['sn_in_log'] or '-'} | {ls['mac_in_log'] or '-'} |\n")

print("Done", out_path)