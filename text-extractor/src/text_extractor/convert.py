"""PDF to layout markdown (see text-extractor/specs/output-format.md)."""

import io
import logging
import time
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2

from .ocr import Ocr, OcrError

# pdfminer warns about font descriptors on most run-001 documents; not useful.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Characters this large are watermarks: run-001 body text is at most 30 pt,
# watermark letters at least 226 pt.
WATERMARK_MIN_SIZE = 72.0


@dataclass
class PdfInfo:
    """Facts about the PDF, used for the diagnostics file."""

    page_count: int | None = None
    chars_per_page: list[int] | None = None
    images_per_page: list[int] | None = None
    page_kinds: list[str] | None = None
    ocr_chars_per_page: list[int | None] | None = None
    pdf_metadata: dict[str, str] | None = None


class ConversionError(Exception):
    """A PDF reading problem; carries what was learned about the PDF."""

    code = ""

    def __init__(self, message: str, info: PdfInfo | None = None):
        super().__init__(message)
        self.info = info or PdfInfo()


class PdfUnreadable(ConversionError):
    code = "pdf_unreadable"


class NoTextLayer(ConversionError):
    code = "no_text_layer"


class OcrFailed(ConversionError):
    code = "ocr_failed"


@dataclass
class Result:
    markdown: str
    info: PdfInfo = field(default_factory=PdfInfo)
    ocr_pages: list[int] = field(default_factory=list)  # 1-based
    ocr_duration_ms: int = 0


def convert(pdf_bytes: bytes, document_id: str, ocr: Ocr | None = None) -> Result:
    """Convert a PDF to layout markdown; scanned pages are OCRed (see specs/ocr.md).

    Raises PdfUnreadable if the PDF cannot be opened or parsed, OcrFailed if OCR
    of a page fails, NoTextLayer if no page has text after text extraction and OCR.
    """
    ocr = ocr or Ocr()
    info = PdfInfo()
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            info.page_count = len(pdf.pages)
            info.pdf_metadata = _metadata(pdf.metadata)
            info.chars_per_page, info.images_per_page, info.page_kinds = [], [], []
            pages = []
            for page in pdf.pages:
                page = page.filter(_not_watermark)
                chars = sum(1 for c in page.chars if not c["text"].isspace())
                info.chars_per_page.append(chars)
                info.images_per_page.append(len(page.images))
                info.page_kinds.append("text" if chars else "scan" if page.images else "empty")
                pages.append(_normalize(page.extract_text(layout=True)) if chars else "")
    except Exception as e:  # pdfminer raises many exception types for bad input
        raise PdfUnreadable(f"cannot read PDF: {type(e).__name__}: {e}", info) from e

    info.ocr_chars_per_page = [None] * info.page_count
    ocr_pages = [i for i, kind in enumerate(info.page_kinds) if kind == "scan"]
    start = time.monotonic()
    if ocr_pages:
        try:
            doc = pypdfium2.PdfDocument(pdf_bytes)
        except Exception as e:
            raise PdfUnreadable(f"cannot render PDF: {type(e).__name__}: {e}", info) from e
        try:
            for i in ocr_pages:
                try:
                    pages[i] = _normalize(ocr.page_text(doc, i))
                except OcrError as e:
                    raise OcrFailed(f"page {i + 1}: {e}", info) from e
                info.ocr_chars_per_page[i] = sum(1 for c in pages[i] if not c.isspace())
        finally:
            doc.close()

    if not any(pages):
        raise NoTextLayer("no page contains text", info)
    ocr_pages = [i + 1 for i in ocr_pages]
    return Result(
        markdown=_render(document_id, pages, ocr_pages),
        info=info,
        ocr_pages=ocr_pages,
        ocr_duration_ms=round((time.monotonic() - start) * 1000),
    )


def _not_watermark(obj: dict) -> bool:
    return obj.get("object_type") != "char" or obj["size"] < WATERMARK_MIN_SIZE


def _normalize(text: str) -> str:
    """Strip trailing spaces, collapse blank-line runs, remove the common left margin."""
    lines: list[str] = []
    for line in text.splitlines():
        line = line.rstrip()
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    indent = min((len(line) - len(line.lstrip()) for line in lines if line), default=0)
    return "\n".join(line[indent:] for line in lines)


def _render(document_id: str, pages: list[str], ocr_pages: list[int] = ()) -> str:
    n = len(pages)
    header = f"document_id: {document_id}; pages: {n}"
    if ocr_pages:
        header += f"; ocr_pages: {','.join(map(str, ocr_pages))}"
    out = [f"<!-- {header} -->", ""]
    for i, text in enumerate(pages, 1):
        fence = "```"
        while fence in text:
            fence += "`"
        heading = f"## Page {i} of {n}" + (" (OCR)" if i in ocr_pages else "")
        out += [heading, "", fence + "text"]
        if text:
            out.append(text)
        out += [fence, ""]
    return "\n".join(out)


def _metadata(meta: dict) -> dict[str, str]:
    out = {}
    for k, v in (meta or {}).items():
        if isinstance(v, bytes):
            v = v.decode("utf-8", errors="replace")
        out[str(k)] = str(v)
    return out
