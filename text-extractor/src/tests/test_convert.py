import io

import pypdf
import pytest

from tests import pdfs
from text_extractor.convert import NoTextLayer, PdfUnreadable, _normalize, _render, convert

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
    with pytest.raises(NoTextLayer) as exc:
        convert(pdfs.build([[], []]), DOC)
    assert exc.value.code == "no_text_layer"
    assert exc.value.info.page_count == 2
    assert exc.value.info.chars_per_page == [0, 0]
    assert exc.value.info.images_per_page == [0, 0]


def test_only_watermark_text_is_no_text_layer():
    with pytest.raises(NoTextLayer) as exc:
        convert(pdfs.build([[(100, 300, 300, "DRAFT")]]), DOC)
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
