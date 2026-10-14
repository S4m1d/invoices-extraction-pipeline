"""PDF to layout markdown (see text-extractor/specs/output-format.md)."""

import io
import logging
from dataclasses import dataclass, field

import pdfplumber

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


@dataclass
class Result:
    markdown: str
    info: PdfInfo = field(default_factory=PdfInfo)


def convert(pdf_bytes: bytes, document_id: str) -> Result:
    """Convert a PDF to layout markdown.

    Raises PdfUnreadable if the PDF cannot be opened or parsed, NoTextLayer if
    no page has text after watermark removal.
    """
    info = PdfInfo()
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            info.page_count = len(pdf.pages)
            info.pdf_metadata = _metadata(pdf.metadata)
            info.chars_per_page, info.images_per_page = [], []
            pages = []
            for page in pdf.pages:
                page = page.filter(_not_watermark)
                info.chars_per_page.append(sum(1 for c in page.chars if not c["text"].isspace()))
                info.images_per_page.append(len(page.images))
                pages.append(_normalize(page.extract_text(layout=True)))
    except Exception as e:  # pdfminer raises many exception types for bad input
        raise PdfUnreadable(f"cannot read PDF: {type(e).__name__}: {e}", info) from e

    if not any(info.chars_per_page):
        raise NoTextLayer("no page contains text", info)
    return Result(markdown=_render(document_id, pages), info=info)


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


def _render(document_id: str, pages: list[str]) -> str:
    n = len(pages)
    out = [f"<!-- document_id: {document_id}; pages: {n} -->", ""]
    for i, text in enumerate(pages, 1):
        fence = "```"
        while fence in text:
            fence += "`"
        out += [f"## Page {i} of {n}", "", fence + "text"]
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
