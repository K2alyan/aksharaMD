from __future__ import annotations
import csv as _csv
import io
from pathlib import Path

from ..base import ParserPlugin
from ..registry import register_parser
from ...context import CompilationContext
from ...models.block import Block, BlockType
from ...models.document import Document

_MAX_ROWS_PER_SHEET = 500   # cap rows to avoid token explosion on huge files
_MAX_COLS = 20


def _rows_to_markdown(headers: list[str], rows: list[list[str]]) -> str:
    def _trunc(s: str, n: int = 80) -> str:
        s = str(s).replace("\n", " ").strip()
        return s[:n] + "…" if len(s) > n else s

    cols = min(len(headers), _MAX_COLS)
    header_row = "| " + " | ".join(_trunc(h) for h in headers[:cols]) + " |"
    sep_row = "| " + " | ".join(["---"] * cols) + " |"
    data_rows = [
        "| " + " | ".join(_trunc(str(c)) for c in row[:cols]) + " |"
        for row in rows
    ]
    return "\n".join([header_row, sep_row] + data_rows)


# ── XLSX ──────────────────────────────────────────────────────────────────────

class XlsxParser(ParserPlugin):
    name = "xlsx_parser"
    supported_types = ["xlsx", "xlsm", "xltx", "xltm"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import openpyxl

        path = Path(ctx.source)
        try:
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        except Exception as e:
            ctx.error("XLSX_PARSE_ERROR", str(e))
            return ctx

        blocks: list[Block] = []
        idx = 0

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            blocks.append(Block(type=BlockType.HEADING, content=sheet_name, level=2, index=idx))
            idx += 1

            all_rows = list(ws.iter_rows(values_only=True))
            if not all_rows:
                continue

            headers = [str(c) if c is not None else "" for c in all_rows[0]]
            data = [
                [str(c) if c is not None else "" for c in row]
                for row in all_rows[1:_MAX_ROWS_PER_SHEET + 1]
            ]

            if data:
                md = _rows_to_markdown(headers, data)
                omitted = len(all_rows) - 1 - len(data)
                if omitted > 0:
                    md += f"\n\n*({omitted} additional rows omitted)*"
                blocks.append(Block(type=BlockType.TABLE, content=md, index=idx))
                idx += 1

        wb.close()
        ctx.document = Document(
            source=str(path),
            file_type=Path(path).suffix.lstrip(".").lower(),
            title=path.stem,
            pages=len(wb.sheetnames),
            blocks=blocks,
            metadata={"sheets": wb.sheetnames},
        ).compute_id()
        return ctx


# ── XLS (legacy) ──────────────────────────────────────────────────────────────

class XlsParser(ParserPlugin):
    name = "xls_parser"
    supported_types = ["xls"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import xlrd

        path = Path(ctx.source)
        try:
            wb = xlrd.open_workbook(str(path))
        except Exception as e:
            ctx.error("XLS_PARSE_ERROR", str(e))
            return ctx

        blocks: list[Block] = []
        idx = 0

        for sheet in wb.sheets():
            blocks.append(Block(type=BlockType.HEADING, content=sheet.name, level=2, index=idx))
            idx += 1
            if sheet.nrows == 0:
                continue

            headers = [str(sheet.cell_value(0, c)) for c in range(min(sheet.ncols, _MAX_COLS))]
            data = [
                [str(sheet.cell_value(r, c)) for c in range(min(sheet.ncols, _MAX_COLS))]
                for r in range(1, min(sheet.nrows, _MAX_ROWS_PER_SHEET + 1))
            ]
            if data:
                md = _rows_to_markdown(headers, data)
                omitted = sheet.nrows - 1 - len(data)
                if omitted > 0:
                    md += f"\n\n*({omitted} additional rows omitted)*"
                blocks.append(Block(type=BlockType.TABLE, content=md, index=idx))
                idx += 1

        ctx.document = Document(
            source=str(path),
            file_type="xls",
            title=path.stem,
            pages=wb.nsheets,
            blocks=blocks,
            metadata={"sheets": wb.sheet_names()},
        ).compute_id()
        return ctx


# ── CSV ───────────────────────────────────────────────────────────────────────

class CsvParser(ParserPlugin):
    name = "csv_parser"
    supported_types = ["csv", "tsv"]

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        import chardet

        path = Path(ctx.source)
        raw = path.read_bytes()
        enc = chardet.detect(raw).get("encoding") or "utf-8"

        ext = path.suffix.lstrip(".").lower()
        delimiter = "\t" if ext == "tsv" else ","

        try:
            text = raw.decode(enc, errors="replace")
            reader = _csv.reader(io.StringIO(text), delimiter=delimiter)
            all_rows = [row for row in reader if any(c.strip() for c in row)]
        except Exception as e:
            ctx.error("CSV_PARSE_ERROR", str(e))
            return ctx

        if not all_rows:
            ctx.document = Document(source=str(path), file_type=ext, title=path.stem, blocks=[]).compute_id()
            return ctx

        headers = all_rows[0]
        data = all_rows[1:_MAX_ROWS_PER_SHEET + 1]
        md = _rows_to_markdown(headers, data)
        omitted = len(all_rows) - 1 - len(data)

        blocks: list[Block] = [
            Block(type=BlockType.METADATA,
                  content=f"File: {path.name} | Columns: {len(headers)} | Rows: {len(all_rows)-1}",
                  index=0),
            Block(type=BlockType.TABLE, content=md, index=1),
        ]
        if omitted > 0:
            blocks.append(Block(type=BlockType.PARAGRAPH,
                                content=f"*({omitted} additional rows omitted from preview)*",
                                index=2))

        ctx.document = Document(
            source=str(path),
            file_type=ext,
            title=path.stem,
            pages=1,
            blocks=blocks,
            metadata={"columns": len(headers), "rows": len(all_rows) - 1},
        ).compute_id()
        return ctx


register_parser("xlsx", XlsxParser)
register_parser("xlsm", XlsxParser)
register_parser("xls",  XlsParser)
register_parser("csv",  CsvParser)
register_parser("tsv",  CsvParser)
