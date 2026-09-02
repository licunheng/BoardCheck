# -*- coding: utf-8 -*-
"""
Build FD01G 240→SN report as formatted .xlsx
- 缺失行整行标红
- 表头深色 + 冻结 + 筛选
- 列宽/字体/对齐按中文习惯
"""
import json
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, NamedStyle
)
from openpyxl.utils import get_column_letter

SRC = Path(r"D:/000AI_project_achieve/agent_project_plan/agent/_work/trace_to_sn_v3.json")
OUT = Path(r"D:/000AI_project_achieve/agent_project_plan/agent/_work/FD01G_追溯码240_SN映射.xlsx")

rows = json.load(open(SRC, encoding="utf-8"))

# 统计
n_total = len(rows)
n_ok = sum(1 for r in rows if r["sn"])
n_miss = n_total - n_ok

wb = Workbook()

# ============ Sheet 1: 全量 240 行 ============
ws = wb.active
ws.title = "240行明细"

# 颜色
HDR_FILL = PatternFill("solid", fgColor="1F4E78")  # 深蓝
HDR_FONT = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
ALT_FILL = PatternFill("solid", fgColor="F2F6FC")  # 浅蓝条纹
MISS_FILL = PatternFill("solid", fgColor="FFC7CE")  # 缺失浅红
MISS_FONT = Font(name="微软雅黑", size=10, color="9C0006", bold=True)
OK_FILL = PatternFill("solid", fgColor="FFFFFF")
OK_FONT = Font(name="微软雅黑", size=10, color="1F4E78")
MONO_FONT = Font(name="Consolas", size=10, color="000000")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
THIN = Side(style="thin", color="B4C7E7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# 标题行
ws["A1"] = "FD01G 追溯码 240 → SN 全量映射(日志版)"
ws["A1"].font = Font(name="微软雅黑", size=14, bold=True, color="FFFFFF")
ws["A1"].fill = HDR_FILL
ws["A1"].alignment = CENTER
ws.merge_cells("A1:G1")
ws.row_dimensions[1].height = 32

# 副标题统计
ws["A2"] = f"输入追溯码 {n_total}  |  日志可定位 SN {n_ok}  |  日志无 SN {n_miss}  |  配对策略:同 COM 内 trace 回复时间就近匹配  |  数据源:Info-2026-05-21/25/26/27/30 共 5 份板检日志"
ws["A2"].font = Font(name="微软雅黑", size=10, color="1F4E78", italic=True)
ws["A2"].alignment = CENTER
ws.merge_cells("A2:G2")
ws.row_dimensions[2].height = 22

# 表头
headers = ["序号", "追溯码", "SN(32位hex)", "SN 形态", "派生 MAC", "日志来源", "关联位置(行号)"]
for i, h in enumerate(headers, 1):
    c = ws.cell(row=3, column=i, value=h)
    c.font = HDR_FONT
    c.fill = HDR_FILL
    c.alignment = CENTER
    c.border = BORDER
ws.row_dimensions[3].height = 26

# 数据
for i, r in enumerate(rows, start=4):
    is_miss = not r["sn"]
    sn = (r["sn"] or "").lower()
    fmt = r["sn_format"] or ""
    src = r["source"] or ""
    lineno = r["sn_lineno"] or ""
    mac = r["trace"][-12:].upper()
    vals = [r["idx"], r["trace"], sn, fmt, mac, src, lineno]
    for j, v in enumerate(vals, 1):
        c = ws.cell(row=i, column=j, value=v)
        c.border = BORDER
        c.alignment = CENTER if j in (1, 3, 4, 5, 6, 7) else LEFT
        if is_miss:
            c.fill = MISS_FILL
            c.font = MISS_FONT if j != 3 else MISS_FONT
            if j == 2 or j == 5:
                c.font = Font(name="Consolas", size=10, color="9C0006", bold=True)
        else:
            c.fill = ALT_FILL if i % 2 == 0 else OK_FILL
            if j in (2, 3, 5):
                c.font = Font(name="Consolas", size=10, color="1F4E78")
            else:
                c.font = OK_FONT

# 列宽
widths = [8, 28, 38, 10, 18, 18, 14]
for i, w in enumerate(widths, 1):
    ws.column_dimensions[get_column_letter(i)].width = w

# 冻结 + 筛选
ws.freeze_panes = "A4"
ws.auto_filter.ref = f"A3:G{3 + n_total}"

# ============ Sheet 2: 统计 ============
ws2 = wb.create_sheet("统计")

ws2["A1"] = "FD01G 追溯码 240 → SN 统计"
ws2["A1"].font = Font(name="微软雅黑", size=14, bold=True, color="FFFFFF")
ws2["A1"].fill = HDR_FILL
ws2["A1"].alignment = CENTER
ws2.merge_cells("A1:C1")
ws2.row_dimensions[1].height = 32

stat_rows = [
    ("输入追溯码总数", n_total, ""),
    ("日志可定位 SN", n_ok, f"{n_ok/n_total*100:.1f}%"),
    ("日志无 SN(缺失)", n_miss, f"{n_miss/n_total*100:.1f}%"),
    ("SN 形态 ① 旧式 收 :id:", 376, "5/21 老批次"),
    ("SN 形态 ② 收:560D0410<SN>18", 1896, "5/25+ 新协议应答"),
    ("SN 形态 ③ 收:560E0410<SN>", 139, "5/26+ 授权包携带"),
    ("关联时间窗", "30 秒", "trace 回复网关SN 后最近 SN 事件"),
]
hdr2 = ["项", "值", "说明"]
for i, h in enumerate(hdr2, 1):
    c = ws2.cell(row=3, column=i, value=h)
    c.font = HDR_FONT
    c.fill = HDR_FILL
    c.alignment = CENTER
    c.border = BORDER
ws2.row_dimensions[3].height = 26

for i, (k, v, note) in enumerate(stat_rows, start=4):
    is_miss_row = k == "日志无 SN(缺失)"
    ws2.cell(row=i, column=1, value=k).border = BORDER
    ws2.cell(row=i, column=2, value=v).border = BORDER
    ws2.cell(row=i, column=3, value=note).border = BORDER
    for col in (1, 2, 3):
        cell = ws2.cell(row=i, column=col)
        cell.alignment = CENTER
        if is_miss_row:
            cell.fill = MISS_FILL
            cell.font = MISS_FONT
        elif i % 2 == 0:
            cell.fill = ALT_FILL
            cell.font = OK_FONT

ws2.column_dimensions["A"].width = 32
ws2.column_dimensions["B"].width = 14
ws2.column_dimensions["C"].width = 32
ws2.row_dimensions[3].height = 26

# ============ Sheet 3: 缺失 2 项详情 ============
ws3 = wb.create_sheet("缺失详情")

ws3["A1"] = "缺失追溯码 2 项排查"
ws3["A1"].font = Font(name="微软雅黑", size=14, bold=True, color="FFFFFF")
ws3["A1"].fill = HDR_FILL
ws3["A1"].alignment = CENTER
ws3.merge_cells("A1:E1")
ws3.row_dimensions[1].height = 32

hdr3 = ["序号", "追溯码", "派生 MAC", "CSV 创建时间", "排查结论"]
for i, h in enumerate(hdr3, 1):
    c = ws3.cell(row=3, column=i, value=h)
    c.font = HDR_FONT
    c.fill = HDR_FILL
    c.alignment = CENTER
    c.border = BORDER
ws3.row_dimensions[3].height = 26

miss_rows = [
    (5, "F233QSHI5MF0161DAE5726", "F0161DAE5726", "2026-05-22 10:00:09",
     "派生 MAC 在 5 份 Info 日志零命中;创建时间 5/22 超出本工程日志覆盖范围(仅 5/21/25/26/27/30)。"),
    (6, "F233QSHI5N8C94DF65A39E", "8C94DF65A39E", "2026-05-23 11:05:27",
     "派生 MAC 在 5 份 Info 日志零命中;创建时间 5/23 同上,需补 Info-2026-05-22.txt 与 Info-2026-05-23.txt。"),
]
for i, (idx, trace, mac, ts, note) in enumerate(miss_rows, start=4):
    vals = [idx, trace, mac, ts, note]
    for j, v in enumerate(vals, 1):
        c = ws3.cell(row=i, column=j, value=v)
        c.fill = MISS_FILL
        c.font = MISS_FONT if j != 1 and j != 4 else MISS_FONT
        if j in (2, 3):
            c.font = Font(name="Consolas", size=10, color="9C0006", bold=True)
        c.alignment = CENTER if j != 5 else LEFT
        c.border = BORDER

ws3.column_dimensions["A"].width = 8
ws3.column_dimensions["B"].width = 30
ws3.column_dimensions["C"].width = 18
ws3.column_dimensions["D"].width = 22
ws3.column_dimensions["E"].width = 60

# 保存
wb.save(OUT)
print("OK:", OUT)