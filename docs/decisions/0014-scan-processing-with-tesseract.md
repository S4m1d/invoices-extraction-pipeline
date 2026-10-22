# 0014. Scan processing: per-page OCR with Tesseract in text-extractor

- Status: accepted
- Date: 2026-10-22

## Context

The MVP handles only clean text PDFs (0007); scanned PDFs fail with `no_text_layer`. Dataset `run003` (10 light scans, one full-page JPEG per page, no text layer) is the first scan dataset. 0003 reserved scan handling for `text-extractor`, behind the existing PDF in, markdown out contract.

Options considered for the OCR engine: Tesseract (local, small, deterministic, word bounding boxes); deep-learning OCR such as PaddleOCR or docTR (more accurate on poor scans, but a 1-2 GB image and slow on CPU); LLM vision through the provider abstraction (accurate, but API cost, not deterministic, can make up text, and brings the LLM into a second module).

## Decision

- `text-extractor` OCRs scanned pages with Tesseract 5 (via pytesseract); pages are rendered with pypdfium2.
- The scan-or-text decision is made per page: a page with no characters after watermark removal and at least one image is OCRed; all other pages keep the pdfplumber flow. Mixed documents are supported.
- OCR word boxes are assembled onto pdfplumber's layout character grid, so `layout.md` keeps one format.
- OCRed pages are marked in `layout.md` (`## Page i of N (OCR)` and `ocr_pages` in the header comment), so the reader knows the text may contain recognition errors.

Details: `text-extractor/specs/ocr.md`.

## Consequences

- The MVP scope of 0007 ("no OCR") is lifted for light scans; 0007 is superseded by this ADR.
- The `text-extractor` image grows by the Tesseract binary and English language data (tens of MB).
- Processing a scanned page takes seconds instead of milliseconds; throughput is scaled by running more containers.
- `fields-extractor` sees a new heading suffix `(OCR)`; its prompt should mention it (separate change in that module).
- Rotated, skewed, heavily degraded and handwritten scans are still out of scope.
