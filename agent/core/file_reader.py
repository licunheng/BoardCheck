"""文件读取器:支持文本日志(.txt/.log) 与 SQLite 数据库(.db/.sqlite)。

按设备规则中的 `file_type` 字段自动选择读取策略。
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class RawRecord:
    """单条原始记录的统一表示。

    来源透明:无论从文本日志抽取还是从 DB 读取,都映射为该结构。
    """

    source_file: str  # 来源文件路径
    line_number: Optional[int]  # 行号(文本日志)或主键(DB)
    raw_text: Optional[str] = None  # 原始整行(文本日志)
    fields: Dict[str, Any] = field(default_factory=dict)  # 已识别字段

    def __repr__(self) -> str:
        keys = list(self.fields.keys())[:3]
        preview = ", ".join(f"{k}={self.fields[k]!r}" for k in keys)
        return f"RawRecord({self.source_file}:{self.line_number} {preview})"


# === 文件类型检测 ===

_TEXT_EXT = {".txt", ".log", ".md"}
_DB_EXT = {".db", ".sqlite", ".sqlite3"}
_CSV_EXT = {".csv", ".xls", ".xlsx"}


def detect_file_type(path: Path) -> str:
    """根据扩展名检测文件类型。"""
    suffix = path.suffix.lower()
    if suffix in _TEXT_EXT:
        return "text_log"
    if suffix in _DB_EXT:
        return "sqlite"
    if suffix in _CSV_EXT:
        return "csv"
    raise ValueError(f"不支持的文件类型: {path.suffix}")


# === 文本日志读取 ===

_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1")


def read_text_log(path: Path) -> List[str]:
    """读取文本日志,自动尝试多种编码。"""
    last_err: Optional[Exception] = None
    for enc in _ENCODINGS:
        try:
            with path.open("r", encoding=enc, errors="strict") as f:
                return f.readlines()
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
            continue
    # 最终兜底:用 errors="replace" 强制读取
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


# === SQLite 读取 ===

def list_sqlite_tables(path: Path) -> List[str]:
    """列出 SQLite 数据库中的所有表名。"""
    with sqlite3.connect(str(path)) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [row[0] for row in cur.fetchall()]


def read_sqlite_table(
    path: Path, table_name: str, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """读取 SQLite 某张表的全部记录。

    返回 list of dict,key 为列名,value 为原始值。
    """
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(f'SELECT * FROM "{table_name}"')
        if limit:
            cur = conn.execute(f'SELECT * FROM "{table_name}" LIMIT ?', (limit,))
        return [dict(row) for row in cur.fetchall()]


# === 表格(CSV / XLS / XLSX / HTML)读取 ===

_XLSX_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def read_spreadsheet(path: Path) -> List[Dict[str, str]]:
    """读取平台表格文件(.xls / .xlsx / .csv),按「文件内容」嗅探而非扩展名。

    平台导出的原生格式可能是真 .xls、.xlsx、或被改名/转存的 CSV/HTML,
    这里按魔数自动识别,避免因扩展名与内容不符导致表头漂移。
    """
    data = path.read_bytes()
    head = data[:8]
    if head.startswith(_XLSX_MAGIC):
        return _read_xlsx(path)
    if head.startswith(_OLE2_MAGIC):
        return _read_xls(path)

    # 文本类:可能是 CSV 或 HTML 表格
    text = None
    for enc in _ENCODINGS:
        try:
            text = data.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")

    if text.lstrip().startswith("<"):
        return _read_html_table(text)
    return _read_csv_text(text)


def read_csv(path: Path) -> List[Dict[str, str]]:
    """读取 CSV(兼容旧调用,内部统一走内容嗅探)。"""
    return read_spreadsheet(path)


def _read_csv_text(text: str) -> List[Dict[str, str]]:
    """从 CSV 文本解析记录(自动嗅探分隔符)。"""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # 默认逗号

    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    # 清理 None key 与首列可能残留的 BOM
    rows = []
    for row in reader:
        cleaned = {}
        for k, v in row.items():
            if k is None:
                continue
            cleaned[k.lstrip("\ufeff").strip()] = v
        rows.append(cleaned)
    return rows


def _read_xlsx(path: Path) -> List[Dict[str, str]]:
    """读取 .xlsx(openpyxl)。"""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ImportError("读取 xlsx 需要 openpyxl,请安装后重试")

    wb = load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    out: List[Dict[str, str]] = []
    header: List[str] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            header = [("" if c is None else str(c).strip().lstrip("\ufeff")) for c in row]
            continue
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        rec: Dict[str, str] = {}
        for j, val in enumerate(row):
            if j >= len(header):
                break
            key = header[j]
            if not key:
                continue
            rec[key] = "" if val is None else str(val).strip()
        out.append(rec)
    wb.close()
    return out


def _read_xls(path: Path) -> List[Dict[str, str]]:
    """读取真正的 .xls(旧二进制格式,xlrd)。"""
    try:
        import xlrd
    except ImportError:
        raise ImportError("读取旧版 .xls 需要 xlrd,请安装后重试")

    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    header = [str(sheet.cell_value(0, c)).strip().lstrip("\ufeff") for c in range(sheet.ncols)]
    out: List[Dict[str, str]] = []
    for r in range(1, sheet.nrows):
        rec: Dict[str, str] = {}
        for c in range(sheet.ncols):
            key = header[c]
            if not key:
                continue
            val = sheet.cell_value(r, c)
            rec[key] = "" if val is None else str(val).strip()
        if any(v for v in rec.values()):
            out.append(rec)
    return out


class _TableParser(HTMLParser):
    """极简 HTML 表格解析器:抽取第一个 <table> 的所有行。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self._cur_row: List[str] = []
        self._cur_cell: List[str] = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cur_row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cur_cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False
            self._cur_row.append("".join(self._cur_cell).strip())
        elif tag == "tr":
            if self._cur_row:
                self.rows.append(self._cur_row)
            self._cur_row = []

    def handle_data(self, data):
        if self._in_cell:
            self._cur_cell.append(data)


def _read_html_table(text: str) -> List[Dict[str, str]]:
    """读取 HTML 表格(Excel「另存为网页」等)。"""
    parser = _TableParser()
    parser.feed(text)
    if not parser.rows:
        return []
    header = parser.rows[0]
    out: List[Dict[str, str]] = []
    for row in parser.rows[1:]:
        rec: Dict[str, str] = {}
        for j, val in enumerate(row):
            if j >= len(header):
                break
            key = header[j]
            if not key:
                continue
            rec[key] = val
        if any(v for v in rec.values()):
            out.append(rec)
    return out


# === 主入口 ===

def read_raw_file(path: Path) -> List[Union[str, Dict[str, Any]]]:
    """读取原始板检数据,返回统一的中间表示。

    - 文本日志: 返回 List[str],每项为一行原文
    - SQLite:   返回 List[Dict],每项为一行记录的字典
    - CSV:      返回 List[Dict],每项为一行 CSV 记录的字典
    """
    file_type = detect_file_type(path)
    if file_type == "text_log":
        return read_text_log(path)
    if file_type == "sqlite":
        # 返回所有表的数据(由后续步骤选择哪张表)
        # 这里返回一个特殊结构: { "_tables": {...} }
        tables = {}
        for table_name in list_sqlite_tables(path):
            tables[table_name] = read_sqlite_table(path, table_name)
        return [{"_tables": tables}]  # type: ignore[return-value]
    if file_type == "csv":
        return read_csv(path)
    raise ValueError(f"未实现的文件类型: {file_type}")


def extract_db_table(
    path: Path, table_name: str
) -> List[Dict[str, Any]]:
    """从 SQLite 数据库中抽取指定表。"""
    return read_sqlite_table(path, table_name)