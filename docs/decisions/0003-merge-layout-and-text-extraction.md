# 0003. Layout analysis and text extraction are one module

- Status: accepted
- Date: 2026-10-13

## Context

The pipeline steps list layout analysis and text extraction separately. For clean text PDFs, one pdfplumber call does both. When scan processing is added, the work becomes multi-stage.

## Decision

One module (`text-extractor`) covers both steps. External contract: PDF in, layout-preserving markdown out. When scans are supported, it becomes an internal sub-pipeline without changing the external contract.

## Consequences

- Four services in the main pipeline.
- Scan/OCR complexity stays contained in this module.
