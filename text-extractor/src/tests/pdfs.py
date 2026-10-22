"""Tiny hand-built PDFs for tests."""


def build(pages: list[list[tuple[float, float, float, str]]]) -> bytes:
    """Build a PDF. Each page is a list of (x, y, font_size, text) in Helvetica."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objs) + 1 + 2 * len(pages)  # placeholder, fixed below
    page_ids = []
    for texts in pages:
        stream = b"".join(
            b"BT /F1 %g Tf %g %g Td (%s) Tj ET\n" % (size, x, y, t.encode("latin-1")) for x, y, size, t in texts
        )
        content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_id, content, font)
            )
        )
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))) == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
    return bytes(out)


INVOICE = build(
    [
        [
            (72, 720, 12, "ACME Ltd - Invoice INV-001"),
            (72, 690, 10, "Item"),
            (300, 690, 10, "Qty"),
            (400, 690, 10, "Amount"),
            (72, 670, 10, "Widget"),
            (300, 670, 10, "2"),
            (400, 670, 10, "$20.00"),
            (300, 640, 10, "Total"),
            (400, 640, 10, "$20.00"),
        ]
    ]
)

EMPTY = build([[]])


def scan(pages: int = 1) -> bytes:
    """An image-only PDF, one gray full-page image per page (no text layer)."""
    import io

    from PIL import Image

    images = [Image.new("L", (100, 140), 200) for _ in range(pages)]
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def concat(*docs: bytes) -> bytes:
    """Pages of all documents, in order."""
    import io

    import pypdf

    writer = pypdf.PdfWriter()
    for doc in docs:
        writer.append(pypdf.PdfReader(io.BytesIO(doc)))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class FakeOcr:
    """Stands in for Tesseract: returns texts[page index], or raises `error`."""

    def __init__(self, texts: dict[int, str] | None = None, error: Exception | None = None):
        self.texts = texts or {}
        self.error = error
        self.pages: list[int] = []

    def page_text(self, pdf, index: int) -> str:
        self.pages.append(index)
        if self.error:
            raise self.error
        return self.texts.get(index, "")
