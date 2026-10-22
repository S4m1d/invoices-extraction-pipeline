import pytesseract
import pytest

from tests import pdfs
from text_extractor import ocr
from text_extractor.ocr import Ocr, OcrError, Word, assemble, keep


def word(text, x0, top, width=None, height=10.0, conf=95.0):
    return Word(text, x0, top, x0 + (width or 6 * len(text)), top + height, conf)


def test_assemble_places_words_on_layout_grid():
    words = [
        word("Item", 0, 100),
        word("Amount", 290, 101),  # slightly lower, same line
        word("Widget", 0, 126),
        word("$20.00", 290, 125),
    ]
    assert assemble(words).splitlines() == [
        *[""] * 8,  # round(100 / 13) = 8 rows above
        "Item" + " " * 36 + "Amount",  # round(290 / 7.25) = 40
        "",
        "Widget" + " " * 34 + "$20.00",
    ]


def test_assemble_groups_rows_across_blocks_and_orders_left_to_right():
    # Tesseract returns cells of one row from different blocks, out of order.
    words = [word("B", 100, 0, height=12), word("A", 0, 2), word("C", 200, 4)]
    assert assemble(words) == "A" + " " * 13 + "B" + " " * 13 + "C"


def test_assemble_keeps_one_space_between_overlapping_words():
    words = [word("Description", 0, 0, width=120), word("QTY", 60, 0)]
    assert assemble(words) == "Description QTY"


def test_assemble_rows_strictly_increase():
    # Two lines that round to the same grid row stay on separate rows.
    words = [word("one", 0, 0, height=4), word("two", 0, 5, height=4)]
    assert assemble(words) == "one\ntwo"


def test_assemble_empty():
    assert assemble([]) == ""


@pytest.mark.parametrize(
    ("text", "conf", "kept"),
    [
        ("Total", 20.0, True),  # letters kept whatever the confidence
        ("$9.50", 10.0, True),
        ("|", 59.0, False),  # punctuation-only noise
        ("—", 60.0, True),
        ("", 90.0, False),
        ("word", -1.0, False),
    ],
)
def test_keep(text, conf, kept):
    assert keep(text, conf) is kept


def tesseract_data(*rows):
    keys = ("text", "conf", "left", "top", "width", "height")
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


def test_recognize_scales_pixels_to_points_and_filters(monkeypatch):
    calls = {}

    def fake(image, **kw):
        calls.update(kw)
        return tesseract_data(("Total", "96", 200, 400, 100, 30), ("|", "40", 10, 10, 2, 30), ("", "-1", 0, 0, 0, 0))

    monkeypatch.setattr(pytesseract, "image_to_data", fake)
    words = Ocr(lang="eng+deu", page_timeout=5).recognize(None)
    s = 72 / ocr.DPI
    assert words == [Word("Total", 200 * s, 400 * s, 300 * s, 430 * s, 96.0)]
    assert calls["lang"] == "eng+deu" and calls["timeout"] == 5 and calls["config"] == "--oem 1 --psm 3"


@pytest.mark.parametrize(
    ("error", "message"),
    [(RuntimeError("Tesseract process timeout"), "timed out"), (pytesseract.TesseractError(1, "bad"), "failed")],
)
def test_recognize_errors(monkeypatch, error, message):
    def fake(image, **kw):
        raise error

    monkeypatch.setattr(pytesseract, "image_to_data", fake)
    with pytest.raises(OcrError, match=message):
        Ocr().recognize(None)


def test_check_reports_missing_languages(monkeypatch):
    monkeypatch.setattr(pytesseract, "get_tesseract_version", lambda: "5.5.0")
    monkeypatch.setattr(pytesseract, "get_languages", lambda: ["eng", "osd"])
    Ocr("eng").check()
    with pytest.raises(OcrError, match="not installed: deu"):
        Ocr("eng+deu").check()


def test_check_reports_missing_binary(monkeypatch):
    def missing():
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "get_tesseract_version", missing)
    with pytest.raises(OcrError, match="not usable"):
        Ocr().check()


def test_render_page_size():
    import pypdfium2

    doc = pypdfium2.PdfDocument(pdfs.INVOICE)  # 612 x 792 pt
    image = ocr.render(doc, 0)
    assert image.mode == "L" and image.size == (round(612 * ocr.DPI / 72), round(792 * ocr.DPI / 72))
