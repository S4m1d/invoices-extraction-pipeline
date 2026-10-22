import io

import pypdf
import pytest

from tests import pdfs
from text_extractor.convert import NoTextLayer, OcrFailed, PdfUnreadable, _normalize, _render, convert
from text_extractor.ocr import OcrError

DOC = "0f8fad5b-d9cb-469f-a165-70867728950e"


def test_single_page():
    md = convert(pdfs.INVOICE, DOC).markdown
    assert md.startswith(f"<!-- document_id: {DOC}; pages: 1 -->\n\n## Page 1 of 1\n\n```text\n")
    assert md.endswith("```\n")
    assert "Invoice INV-001" in md
    # Columns stay apart on one line.
    assert any("Widget" in line and "$20.00" in line for line in md.splitlines())


def test_pages_in_order_with_numbering():
    pdf = pdfs.build([[(72, 700, 12, "first page")], [(72, 700, 12, "second page")], [(72, 700, 12, "third")]])
    res = convert(pdf, DOC)
    md = res.markdown
    assert [line for line in md.splitlines() if line.startswith("## ")] == [
        "## Page 1 of 3",
        "## Page 2 of 3",
        "## Page 3 of 3",
    ]
    assert md.index("first page") < md.index("second page") < md.index("third")
    assert res.info.page_count == 3


def test_empty_page_inside_text_document_keeps_its_section():
    pdf = pdfs.build([[(72, 700, 12, "page one")], [], [(72, 700, 12, "page three")]])
    md = convert(pdf, DOC).markdown
    assert "## Page 2 of 3\n\n```text\n```\n" in md


def test_no_text_layer():
    ocr = pdfs.FakeOcr()
    with pytest.raises(NoTextLayer) as exc:
        convert(pdfs.build([[], []]), DOC, ocr)
    assert exc.value.code == "no_text_layer"
    assert exc.value.info.page_count == 2
    assert exc.value.info.chars_per_page == [0, 0]
    assert exc.value.info.images_per_page == [0, 0]
    assert exc.value.info.page_kinds == ["empty", "empty"]
    assert exc.value.info.ocr_chars_per_page == [None, None]
    assert ocr.pages == []  # nothing to OCR


def test_text_document_is_not_ocred():
    ocr = pdfs.FakeOcr()
    res = convert(pdfs.INVOICE, DOC, ocr)
    assert ocr.pages == [] and res.ocr_pages == []
    assert res.info.page_kinds == ["text"] and res.info.ocr_chars_per_page == [None]
    assert "(OCR)" not in res.markdown and "ocr_pages" not in res.markdown


def test_scanned_pages_are_ocred_and_marked():
    ocr = pdfs.FakeOcr({0: "   ACME   Invoice   \n\n\n   Total  $9.50", 1: "  page two"})
    res = convert(pdfs.scan(2), DOC, ocr)
    assert ocr.pages == [0, 1]
    assert res.ocr_pages == [1, 2]
    assert res.info.page_kinds == ["scan", "scan"]
    assert res.info.chars_per_page == [0, 0] and res.info.images_per_page == [1, 1]
    assert res.info.ocr_chars_per_page == [21, 7]
    assert res.markdown == (
        f"<!-- document_id: {DOC}; pages: 2; ocr_pages: 1,2 -->\n\n"
        "## Page 1 of 2 (OCR)\n\n```text\nACME   Invoice\n\nTotal  $9.50\n```\n\n"
        "## Page 2 of 2 (OCR)\n\n```text\npage two\n```\n"
    )


def test_mixed_document_ocrs_only_scanned_pages():
    pdf = pdfs.concat(pdfs.build([[(72, 700, 12, "typed page")], []]), pdfs.scan(1))
    ocr = pdfs.FakeOcr({2: "scanned attachment"})
    res = convert(pdf, DOC, ocr)
    assert ocr.pages == [2]
    assert res.info.page_kinds == ["text", "empty", "scan"]
    assert [line for line in res.markdown.splitlines() if line.startswith(("<!--", "## "))] == [
        f"<!-- document_id: {DOC}; pages: 3; ocr_pages: 3 -->",
        "## Page 1 of 3",
        "## Page 2 of 3",
        "## Page 3 of 3 (OCR)",
    ]
    assert "typed page" in res.markdown and "scanned attachment" in res.markdown


def test_scan_without_readable_text_is_no_text_layer():
    with pytest.raises(NoTextLayer) as exc:
        convert(pdfs.scan(2), DOC, pdfs.FakeOcr({0: "  \n "}))
    assert exc.value.info.page_kinds == ["scan", "scan"]
    assert exc.value.info.ocr_chars_per_page == [0, 0]


def test_ocr_error_fails_document():
    with pytest.raises(OcrFailed) as exc:
        convert(pdfs.scan(2), DOC, pdfs.FakeOcr(error=OcrError("tesseract timed out after 60 s")))
    assert exc.value.code == "ocr_failed"
    assert str(exc.value) == "page 1: tesseract timed out after 60 s"
    assert exc.value.info.page_kinds == ["scan", "scan"]


def test_only_watermark_text_is_no_text_layer():
    with pytest.raises(NoTextLayer) as exc:
        convert(pdfs.build([[(100, 300, 300, "DRAFT")]]), DOC, pdfs.FakeOcr())
    assert exc.value.info.chars_per_page == [0]


@pytest.mark.parametrize(("size", "kept"), [(71, True), (72, False), (300, False)])
def test_watermark_cutoff(size, kept):
    pdf = pdfs.build([[(72, 700, 12, "body text"), (72, 400, size, "W")]])
    md = convert(pdf, DOC).markdown
    assert "body text" in md
    assert ("W" in md.replace("<!--", "")) is kept


@pytest.mark.parametrize(
    "data",
    [b"", b"hello", b"%PDF-1.4 garbage", pdfs.INVOICE[: len(pdfs.INVOICE) // 3]],
    ids=["empty", "not a pdf", "garbage", "truncated"],
)
def test_unreadable(data):
    with pytest.raises(PdfUnreadable) as exc:
        convert(data, DOC)
    assert exc.value.code == "pdf_unreadable"


def test_password_protected_is_unreadable():
    reader = pypdf.PdfReader(io.BytesIO(pdfs.INVOICE))
    writer = pypdf.PdfWriter(clone_from=reader)
    writer.encrypt(user_password="secret", owner_password="owner")
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(PdfUnreadable):
        convert(buf.getvalue(), DOC)


def test_fence_is_longer_than_backticks_in_text():
    # Tested on the renderer: Helvetica's standard encoding turns ` into ‘ in test PDFs.
    md = _render(DOC, ["code ``` and ```` here"])
    assert "`````text\ncode ``` and ```` here\n`````\n" in md


def test_metadata_and_counts():
    res = convert(pdfs.INVOICE, DOC)
    assert res.info.chars_per_page and res.info.chars_per_page[0] > 0
    assert res.info.images_per_page == [0]
    assert isinstance(res.info.pdf_metadata, dict)
    assert all(isinstance(v, str) for v in res.info.pdf_metadata.values())


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("   a   \n   b", "a\nb"),  # common left margin removed, trailing spaces stripped
        ("  a\n    b", "a\n  b"),  # relative indent kept
        ("a\n\n\n\nb", "a\n\nb"),  # blank runs collapsed
        ("\n\n  a\n\n", "a"),  # leading/trailing blank lines removed
        ("a    b", "a    b"),  # inner spacing kept
        ("", ""),
        ("   \n  ", ""),
    ],
)
def test_normalize(raw, want):
    assert _normalize(raw) == want
