"""OCR of scanned pages (see text-extractor/specs/ocr.md)."""

import logging
import time
from dataclasses import dataclass

import pypdfium2
import pytesseract
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

# Tuned on run003 (150 DPI light scans): 200 DPI with a 3 px median filter beat 300 and 400 DPI.
DPI = 200
MEDIAN_SIZE = 3
# Punctuation-only words below this confidence come from speckle noise.
NOISE_MAX_CONF = 60.0
# pdfplumber's layout-mode grid, in points per character column / text row.
X_DENSITY = 7.25
Y_DENSITY = 13.0


class OcrError(Exception):
    """Tesseract failed or timed out."""


@dataclass
class Word:
    text: str
    x0: float  # points from the page's top-left corner
    top: float
    x1: float
    bottom: float
    conf: float


class Ocr:
    def __init__(self, lang: str = "eng", page_timeout: float = 60.0):
        self.lang = lang
        self.page_timeout = page_timeout

    def check(self) -> None:
        """Raise OcrError unless tesseract runs and every configured language is installed."""
        try:
            pytesseract.get_tesseract_version()
            installed = set(pytesseract.get_languages())
        except Exception as e:
            raise OcrError(f"tesseract not usable: {type(e).__name__}: {e}") from e
        missing = [lang for lang in self.lang.split("+") if lang not in installed]
        if missing:
            raise OcrError(f"tesseract languages not installed: {', '.join(missing)}")

    def page_text(self, pdf: pypdfium2.PdfDocument, index: int) -> str:
        """Render page `index` and return its OCR text on the layout grid (not yet normalized)."""
        start = time.monotonic()
        image = render(pdf, index)
        words = self.recognize(image)
        log.debug(
            "page OCRed",
            extra={
                "page": index + 1,
                "words": len(words),
                "mean_conf": round(sum(w.conf for w in words) / len(words), 1) if words else None,
                "duration_ms": round((time.monotonic() - start) * 1000),
            },
        )
        return assemble(words)

    def recognize(self, image: Image.Image) -> list[Word]:
        try:
            data = pytesseract.image_to_data(
                image,
                lang=self.lang,
                config="--oem 1 --psm 3",
                timeout=self.page_timeout,
                output_type=pytesseract.Output.DICT,
            )
        except pytesseract.TesseractError as e:  # a RuntimeError subclass, so caught first
            raise OcrError(f"tesseract failed: {e}") from e
        except RuntimeError as e:  # pytesseract raises a plain RuntimeError on timeout
            raise OcrError(f"tesseract timed out after {self.page_timeout:g} s") from e
        except Exception as e:
            raise OcrError(f"tesseract failed: {type(e).__name__}: {e}") from e

        scale = 72 / DPI
        words = []
        for text, conf, left, top, width, height in zip(
            data["text"], data["conf"], data["left"], data["top"], data["width"], data["height"], strict=True
        ):
            text, conf = text.strip(), float(conf)
            if not keep(text, conf):
                continue
            words.append(Word(text, left * scale, top * scale, (left + width) * scale, (top + height) * scale, conf))
        return words


def render(pdf: pypdfium2.PdfDocument, index: int) -> Image.Image:
    """Grayscale page image; the median filter removes scan speckle."""
    image = pdf[index].render(scale=DPI / 72, grayscale=True).to_pil()
    return image.filter(ImageFilter.MedianFilter(MEDIAN_SIZE))


def keep(text: str, conf: float) -> bool:
    if not text or conf < 0:
        return False
    if not any(c.isalnum() for c in text) and conf < NOISE_MAX_CONF:
        return False
    return True


def assemble(words: list[Word]) -> str:
    """Place words on pdfplumber's layout character grid."""
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.top + w.bottom) / 2):
        centre = (w.top + w.bottom) / 2
        for line in reversed(lines):
            if min(x.top for x in line) <= centre <= max(x.bottom for x in line):
                line.append(w)
                break
        else:
            lines.append([w])

    out: list[str] = []
    row = -1
    for line in sorted(lines, key=lambda line: min(w.top for w in line)):
        r = max(row + 1, round(min(w.top for w in line) / Y_DENSITY))
        out += [""] * (r - row - 1)
        row = r
        text = ""
        for w in sorted(line, key=lambda w: w.x0):
            col = round(w.x0 / X_DENSITY)
            text += " " * max(col - len(text), 1 if text else 0) + w.text
        out.append(text)
    return "\n".join(out)
