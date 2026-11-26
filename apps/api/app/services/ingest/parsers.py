"""Turn uploaded files into text sections that keep where they came from (page, heading, sheet, rows).

Every parser runs on bytes already in memory (the upload limit bounds them) and refuses archives that
would expand far beyond their size, so a crafted DOCX or XLSX can't exhaust the worker.
"""

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field

from charset_normalizer import from_bytes

from app.models.knowledge import FILE_TYPES

MAX_UNZIPPED = 300 * 1024 * 1024  # total uncompressed size allowed inside a DOCX/XLSX
MAX_ROWS = 200_000
ROWS_PER_SECTION = 40

MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "txt": "text/plain",
}


class ParseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Section:
    text: str
    meta: dict = field(default_factory=dict)


def detect_type(filename: str, data: bytes) -> str:
    """The file type from its extension, confirmed by its content. Raises ParseError when they disagree."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in FILE_TYPES:
        raise ParseError("unsupported_type", "Upload a PDF, DOCX, TXT, CSV or XLSX file.")
    if not data:
        raise ParseError("empty_document", "This file is empty.")
    if ext == "pdf":
        if not data[:1024].lstrip().startswith(b"%PDF-"):
            raise ParseError("type_mismatch", "This file isn't a valid PDF.")
    elif ext in ("docx", "xlsx"):
        marker = "word/document.xml" if ext == "docx" else "xl/workbook.xml"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile:
            raise ParseError("type_mismatch", f"This file isn't a valid {ext.upper()} file.") from None
        if marker not in names:
            raise ParseError("type_mismatch", f"This file isn't a valid {ext.upper()} file.")
    else:
        if b"\x00" in data[:8192]:
            raise ParseError("type_mismatch", f"This file isn't a plain-text {ext.upper()} file.")
    return ext


def _check_zip(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
            raise ParseError("file_too_complex", "This file expands to more data than Verity can process.")


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Detection is reliable on longer samples; short ones are usually Windows-1252 (Excel's CSV export).
    if len(data) >= 2048:
        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    return data.decode("cp1252", errors="replace")


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ParseError("encrypted_pdf", "This PDF is password-protected. Upload an unprotected copy.")
        sections = []
        for i, page in enumerate(reader.pages, start=1):
            text = clean(page.extract_text() or "")
            if text:
                sections.append(Section(text, {"page": i}))
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as e:
        raise ParseError("corrupt_file", "This PDF is damaged and couldn't be read.") from e
    if not sections:
        raise ParseError("no_text_layer", "No text layer found. This PDF looks scanned; upload a text-based PDF.")
    return sections


def parse_docx(data: bytes) -> list[Section]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    _check_zip(data)
    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as e:  # python-docx raises a variety of errors on bad input
        raise ParseError("corrupt_file", "This DOCX file is damaged and couldn't be read.") from e

    sections: list[Section] = []
    path: list[str] = []
    buf: list[str] = []

    def flush():
        text = clean("\n\n".join(buf))
        if text:
            heading = " › ".join(path)
            sections.append(Section(text, {"heading": heading} if heading else {}))
        buf.clear()

    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph):
            text = block.text.strip()
            style = (block.style.name or "").lower() if block.style is not None else ""
            m = re.match(r"heading (\d)", style)
            if (m or style == "title") and text:
                flush()
                level = int(m.group(1)) if m else 1
                del path[level - 1 :]
                path.append(text)
            elif text:
                buf.append(text)
        elif isinstance(block, Table):
            rows = []
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                buf.append("\n".join(rows))
    flush()
    if not sections:
        raise ParseError("empty_document", "This document has no text.")
    return sections


def _rows_to_sections(rows: list[list[str]], meta: dict) -> list[Section]:
    """Row groups rendered as `header: value` lines, so each chunk is understandable on its own."""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [h.strip() or f"Column {i + 1}" for i, h in enumerate(rows[0])]
    body = rows[1:] or [rows[0]]
    if not rows[1:]:
        header = [f"Column {i + 1}" for i in range(len(rows[0]))]
    sections = []
    for start in range(0, len(body), ROWS_PER_SECTION):
        group = body[start : start + ROWS_PER_SECTION]
        lines = []
        for r in group:
            pairs = [f"{header[i] if i < len(header) else f'Column {i + 1}'}: {v.strip()}" for i, v in enumerate(r) if v.strip()]
            if pairs:
                lines.append("; ".join(pairs))
        if lines:
            first, last = start + 2, start + 1 + len(group)  # 1-based, after the header row
            sections.append(Section("\n".join(lines), {**meta, "rows": f"{first}-{last}"}))
    return sections


def parse_csv(data: bytes) -> list[Section]:
    text = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    csv.field_size_limit(1_000_000)
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i >= MAX_ROWS:
            break
        rows.append(row)
    sections = _rows_to_sections(rows, {})
    if not sections:
        raise ParseError("empty_document", "This CSV file has no data.")
    return sections


def parse_xlsx(data: bytes) -> list[Section]:
    import openpyxl

    _check_zip(data)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ParseError("corrupt_file", "This XLSX file is damaged and couldn't be read.") from e
    sections, total = [], 0
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                total += 1
                if total > MAX_ROWS:
                    break
                rows.append(["" if v is None else str(v) for v in row])
            sections += _rows_to_sections(rows, {"sheet": ws.title})
    finally:
        wb.close()
    if not sections:
        raise ParseError("empty_document", "This spreadsheet has no data.")
    return sections


def parse_txt(data: bytes) -> list[Section]:
    text = clean(decode_text(data))
    if not text:
        raise ParseError("empty_document", "This file has no text.")
    return [Section(text, {})]


PARSERS = {"pdf": parse_pdf, "docx": parse_docx, "xlsx": parse_xlsx, "csv": parse_csv, "txt": parse_txt}


def parse(file_type: str, data: bytes) -> list[Section]:
    return PARSERS[file_type](data)
