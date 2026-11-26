import io
import zipfile

import pytest

from app.services.ingest.chunker import MAX_TOKENS, chunk_sections, estimate_tokens
from app.services.ingest.parsers import ParseError, Section, detect_type, parse
from tests.files import make_blank_pdf, make_docx, make_pdf, make_xlsx


def test_pdf_pages_keep_page_numbers():
    sections = parse("pdf", make_pdf(["Refunds take five days.", "Shipping is free over 50 euros."]))
    assert [s.meta["page"] for s in sections] == [1, 2]
    assert "Refunds take five days" in sections[0].text


def test_scanned_pdf_is_explained():
    with pytest.raises(ParseError) as e:
        parse("pdf", make_blank_pdf())
    assert e.value.code == "no_text_layer"


def test_docx_headings_and_tables():
    data = make_docx(
        [
            ("h1", "Billing"),
            ("p", "Invoices are sent monthly."),
            ("h2", "Refunds"),
            ("p", "Refunds take five days."),
            ("table", "Plan,Price;Starter,29"),
        ]
    )
    sections = parse("docx", data)
    assert sections[0].meta["heading"] == "Billing"
    assert sections[1].meta["heading"] == "Billing › Refunds"
    assert "Starter | 29" in sections[1].text


def test_xlsx_rows_become_labelled_lines():
    data = make_xlsx({"Prices": [["Plan", "Price"], ["Starter", 29], ["Growth", 99]]})
    [s] = parse("xlsx", data)
    assert s.meta == {"sheet": "Prices", "rows": "2-3"}
    assert "Plan: Starter; Price: 29" in s.text


def test_csv_with_semicolons_and_latin1():
    data = "Produkt;Preis\nKäse;4\n".encode("latin-1")
    [s] = parse("csv", data)
    assert "Produkt: Käse; Preis: 4" in s.text


def test_txt_utf8_bom():
    [s] = parse("txt", "﻿Hello  world\r\n\r\n\r\nBye".encode())
    assert s.text == "Hello world\n\nBye"


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("notes.exe", b"MZ", "unsupported_type"),
        ("fake.pdf", b"hello", "type_mismatch"),
        ("fake.docx", b"%PDF-1.4", "type_mismatch"),
        ("binary.txt", b"ab\x00cd", "type_mismatch"),
        ("empty.csv", b"", "empty_document"),
    ],
)
def test_detect_type_rejects(name, data, code):
    with pytest.raises(ParseError) as e:
        detect_type(name, data)
    assert e.value.code == code


def test_detect_type_xlsx_is_not_docx():
    with pytest.raises(ParseError):
        detect_type("a.docx", make_xlsx({"S": [["a"]]}))
    assert detect_type("A.XLSX", make_xlsx({"S": [["a"]]})) == "xlsx"


def test_zip_bomb_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (301 * 1024 * 1024))
    with pytest.raises(ParseError) as e:
        parse("docx", buf.getvalue())
    assert e.value.code == "file_too_complex"


def test_chunks_respect_size_and_overlap():
    para = " ".join(f"Sentence number {i} explains a detail of the product." for i in range(400))
    chunks = chunk_sections([Section(para, {"page": 3})])
    assert len(chunks) > 3
    assert all(c.token_count <= MAX_TOKENS for c in chunks)
    assert all(c.meta == {"page": 3} for c in chunks)
    # consecutive chunks overlap, and sentences of one paragraph stay joined by spaces
    last_sentence = chunks[0].content.rsplit("Sentence number", 1)[1]
    assert last_sentence in chunks[1].content
    assert "\n" not in chunks[0].content


def test_small_pages_merge_with_page_range():
    chunks = chunk_sections([Section("Short page one.", {"page": 1}), Section("Short page two.", {"page": 2})])
    assert len(chunks) == 1
    assert chunks[0].meta == {"page": 1, "page_end": 2}


def test_heading_prefixed():
    [c] = chunk_sections([Section("Refunds take five days.", {"heading": "Billing › Refunds"})])
    assert c.content.startswith("Billing › Refunds\n\n")
    assert c.token_count == estimate_tokens(c.content)
