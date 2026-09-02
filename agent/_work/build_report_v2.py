# -*- coding: utf-8 -*-
"""Build v2 report (log-only, 240 traces)."""
import json
from pathlib import Path

rows = json.load(open(r"D:/000AI_project_achieve/agent_project_plan/agent/_work/trace_to_sn_log_only.json", encoding="utf-8"))
out_path = Path(r"D:/000AI_project_achieve/agent_project_plan/agent/_work/fd01g_240_log_only.md")

with open(out_path, "w", encoding="utf-8") as out:
    out.write("# FD01G 追溯码 240 → SN 全量映射(日志版)\n\n")
    out.write("> **数据源仅板检日志**(无 CSV),路径:`D:/000AI_project_achieve/agent_project_plan/Device_doc/FD01G/FD01G板检问题核验/`\n")
    out.write("> 5 份 Info-*.txt(2026-05-21/25/26/27/30)\n\n")

    out.write("## 结果统计\n\n")
    sn_ok = sum(1 for r in rows if r["sn"])
    miss = [r for r in rows if not r["sn"]]
    out.write(f"- 输入追溯码:**240**\n")
    out.write(f"- 日志可定位 SN:**{sn_ok}** / 240({sn_ok/240*100:.1f}%)\n")
    out.write(f"- 日志无 SN:**{len(miss)}** / 240\n\n")

    out.write("## SN 抽取正则(SN 出现的三种形态)\n\n")
    out.write("| 形态 | 正则 | 出现次数 | 适用 |\n|---|---|---|---|\n")
    out.write("| ① 旧 | `收 :id:<SN>` | 376 | 5/21 老批次 |\n")
    out.write("| ② 新协议应答 | `收:560D0410<SN>18` | 1896 | 5/25+ |\n")
    out.write("| ③ 授权包携带 | `收:560E0410<SN>` | 139 | 5/26+ |\n\n")

    out.write("## 关联策略\n\n")
    out.write(
        "每个 `回复网关SN:<trace>` 事件向后 30 秒时间窗内,找最近的 SN 事件"
        "(无论哪种形态)得到 (trace, SN) 配对。\n\n"
    )

    out.write("## 全量 240 行明细\n\n")
    out.write("| # | 追溯码 | SN | 形态 | 日志日期 | 关联位置 |\n")
    out.write("|---|---|---|---|---|---|\n")
    for r in rows:
        if r["sn"]:
            out.write(f"| {r['idx']} | `{r['trace']}` | `{r['sn']}` | {r['sn_format']} | {r['source']} | L{r['sn_lineno']} |\n")
        else:
            out.write(f"| {r['idx']} | `{r['trace']}` | **缺失** | - | - | - |\n")

    out.write("\n---\n\n")
    out.write("## 仍缺失的 2 项及排查\n\n")
    out.write("| # | 追溯码 | 派生 MAC | CSV 创建时间 | 排查 |\n|---|---|---|---|---|\n")
    for r in miss:
        mac = r["trace"][-12:].upper()
        out.write(
            f"| {r['idx']} | `{r['trace']}` | `{mac}` | "
            f"CSV 显示 2026-05-22/23 板检,但 5 份 Info 日志不含 5/22 和 5/23 |"
        )
        if r["idx"] == 5:
            out.write(" F0161DAE5726 在所有 5 份日志零命中(连 MAC 行都没有) |\n")
        else:
            out.write(" 8C94DF65A39E 同上 |\n")

    out.write(
        "\n**结论**:240 个追溯码中 **238 个(99.2%)可仅从 5 份板检日志反推 SN**,"
        "仅 #5 `#6` 这 2 个追溯码属于 **5/22 与 5/23 的板检**,"
        "而工程目录的 Info 日志不包含这两天(索引文档亦明确 5/22~5/24 没有 FD01G 板检配置信息头),"
        "需补充 `Info-2026-05-22.txt` 与 `Info-2026-05-23.txt` 即可闭环。\n"
    )

print("Done", out_path)