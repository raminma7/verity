"""Builds small real documents for tests (no binary fixtures in the repo)."""

import io


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one text line per page (Helvetica)."""
    objs = []
    n_pages = len(pages)
    page_ids = [3 + i * 2 for i in range(n_pages)]
    font_id = 3 + n_pages * 2
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n_pages} >>")
    for i, text in enumerate(pages):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET"
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {page_ids[i] + 1} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        )
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_blank_pdf() -> bytes:
    pdf = make_pdf(["x"])
    return pdf.replace(b"(x) Tj", b"() Tj")


def make_docx(blocks: list[tuple[str, str]]) -> bytes:
    """blocks: (kind, text) where kind is h1, h2, p, or table (rows separated by ';', cells by ',')."""
    import docx

    d = docx.Document()
    for kind, text in blocks:
        if kind.startswith("h"):
            d.add_heading(text, level=int(kind[1]))
        elif kind == "table":
            rows = [r.split(",") for r in text.split(";")]
            t = d.add_table(rows=len(rows), cols=len(rows[0]))
            for i, r in enumerate(rows):
                for j, c in enumerate(r):
                    t.cell(i, j).text = c
        else:
            d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
