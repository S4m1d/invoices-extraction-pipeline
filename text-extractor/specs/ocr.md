# Text Extractor: Scan Processing (OCR)

Status: approved. Decision record: `docs/decisions/0014-scan-processing-with-tesseract.md`.

How `text-extractor` handles pages that are scans: page images with no text layer. Text pages keep the existing pdfplumber flow (see [`overview.md`](overview.md), [`output-format.md`](output-format.md)). The external contract does not change: PDF in, `layout.md` out (ADR 0003).

## Reference data

`run003`: 10 invoices, 21 pages, all `condition` = `light_scan` (`is_degraded` true, no watermark, no handwriting, locales `en-US`/`en-CA`). Every page is one full-page DCT (JPEG) RGB image of 1242 x 1757 px (A4 at about 150 DPI) with no text characters. Content is upright and straight, with light speckle noise. Before this feature, every run003 document fails with `no_text_layer`.

## Page classification

Each page is classified on its own, after watermark removal (`output-format.md`, Text normalization):

| Page kind | Rule | Processing |
|---|---|---|
| `text` | At least one non-whitespace character | pdfplumber layout text (unchanged) |
| `scan` | No characters, at least one image | OCR (below) |
| `empty` | No characters, no images | Empty block (unchanged) |

- A document can mix kinds, for example typed invoice pages followed by a scanned attachment.
- A page that has both an image and a text layer (for example a scan with an invisible OCR layer added by a scanner) is a `text` page: the existing text layer is used and it is not OCRed again.
- Vector-only pages with no characters and no images (for example lines and boxes only) are `empty`.

## OCR of a `scan` page

1. **Render.** The whole page is rendered with pypdfium2 (already installed with pdfplumber) at 300 DPI, grayscale. The page is rendered rather than the embedded image extracted, so cropping, scaling, rotation in the page's transform and multiple images are all handled the same way.
2. **Recognize.** Tesseract 5 (LSTM engine, `--oem 1`), page segmentation mode 3 (automatic), language from `TEXT_EXTRACTOR_OCR_LANG` (default `eng`). The module reads word-level output (text, bounding box, confidence) through `pytesseract.image_to_data`.
3. **Filter.** Words with empty text or confidence `-1` are dropped. Words made only of punctuation or symbols with a confidence below 60 are dropped; these come from speckle noise. Other words are kept whatever their confidence: a misread digit is better input for `fields-extractor` than a missing one.
4. **Assemble layout text** (below).
5. **Normalize** with the same rules as text pages (`output-format.md`, Text normalization).

No other preprocessing (deskew, denoise, binarization beyond Tesseract's own) is done in this version; run003 does not need it.

The 300 DPI and the confidence threshold of 60 are starting values. They are checked on run003 during implementation, and the spec is updated if they change.

## Layout assembly

OCR output is placed on the same character grid as pdfplumber's layout mode, so an OCRed page looks like a text page of the same invoice:

- Pixel coordinates are converted to PDF points (`pt = px * 72 / 300`).
- Words are grouped into lines: a word joins a line when its vertical centre lies within the vertical extent of the line's words, or else it starts a new line. Lines are ordered top to bottom and words within a line left to right. Grouping is done across Tesseract's blocks, because Tesseract often puts the cells of one table row into different blocks.
- The column of a word is `round(x0 / 7.25)` (pdfplumber's default `x_density`). If that column would overlap the previous word on the line, the word is placed one space after it.
- The row of a line is `round(top / 13)` (pdfplumber's default `y_density`), kept strictly increasing. Rows with no line between them become blank lines, which normalization then collapses to at most one.

## Output marking

OCRed text may contain recognition errors, so it is marked in `layout.md` (`output-format.md`):

- The page heading becomes `## Page {i} of {N} (OCR)`.
- The first-line comment gets an `ocr_pages` field listing the OCRed page numbers.

Note for `fields-extractor`: its prompt describes page headings as `## Page i of N`. Mentioning the `(OCR)` suffix there, and that OCRed text may have misread characters, is a separate change in that module.

## Errors

Changes to the error table in [`overview.md`](overview.md):

| Error code | Cause | Retried | Diagnostics file |
|---|---|---|---|
| `no_text_layer` | After text extraction and OCR, no page contains any text | no | yes |
| `ocr_failed` | Tesseract exits with an error or exceeds `TEXT_EXTRACTOR_OCR_PAGE_TIMEOUT` on a page | no | yes |

- `no_text_layer` keeps its name, so existing failure records and tester views keep working. Its meaning widens: it is now raised only when OCR also finds nothing, for example on a blank or vector-only PDF, or a scan with nothing readable.
- `ocr_failed` is permanent: the same image fails the same way on a retry. One failing page fails the document. Its pages are not returned partly.

## Diagnostics file

The diagnostics file (`overview.md`) gets two fields:

```json
{
  "page_kinds": ["scan", "scan", "text"],
  "ocr_chars_per_page": [1520, 0, null]
}
```

- `page_kinds`: classification per page (`text`, `scan`, `empty`); `null` when the PDF cannot be opened.
- `ocr_chars_per_page`: non-whitespace characters OCR produced per page, `null` for pages not OCRed; `null` as a whole when the PDF cannot be opened.
- `chars_per_page` keeps counting only text-layer characters.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `TEXT_EXTRACTOR_OCR_LANG` | no | `eng` | Tesseract language(s), `+`-separated (e.g. `eng+deu`). Each must be installed in the image. |
| `TEXT_EXTRACTOR_OCR_PAGE_TIMEOUT` | no | `60s` | Time limit for recognizing one page |

At startup the module checks that the `tesseract` binary runs and that every configured language is installed. If not, it logs the error and exits non-zero, so a broken image is caught at deploy time rather than on the first scan.

## Image

The runtime image installs Debian's `tesseract-ocr` and `tesseract-ocr-eng`. Python adds `pytesseract`. Rendering uses pypdfium2, which pdfplumber already depends on; it is declared as a direct dependency.

## Logging

The per-message summary entry gets `ocr_pages` (count) and `ocr_duration_ms`. Each OCRed page logs one debug entry with its page number, word count, mean confidence and duration.

## Performance

OCR runs in the worker process, one page after another. Expect seconds per page on CPU (run003's 7-page invoice is the largest), which is well under RabbitMQ's consumer acknowledgement timeout (30 min by default). Throughput is still scaled by running more containers (`overview.md`, Concurrency).

## Not handled in this version

Recorded as known limitations, not errors:

- Rotated or upside-down scans (no orientation detection), noticeably skewed scans (no deskew).
- Heavily degraded scans, handwriting, photographs of documents.
- Watermarks inside scan images: they are part of the image and may show up as OCR text.
- Languages other than those in `TEXT_EXTRACTOR_OCR_LANG`.
- Partly scanned pages: a page with a text layer *and* a scanned region is treated as `text`, and the scanned region is not OCRed.

## Acceptance

- run-001: `layout.md` output is byte-for-byte unchanged (no page there is a `scan` page).
- run003: all 10 documents produce `layout.md` with every page marked `(OCR)`; none fails.
- Unit tests cover page classification, layout assembly from synthetic word boxes, the `(OCR)` markers, and the `ocr_failed` / `no_text_layer` paths.
- End-to-end field accuracy on run003 is measured with the tester and recorded here. There is no minimum: the first measurement is the baseline for later OCR improvements.
