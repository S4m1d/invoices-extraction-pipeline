# Text Extractor: Overview

## Purpose

Takes an invoice PDF, understands its layout and extracts text while preserving the layout, producing a markdown file.

This module combines two logical pipeline steps, layout analysis and text extraction.

## Stack

Python, pdfplumber.

## Responsibilities

- Consume messages from the ingestion queue.
- Read the PDF from object storage.
- Produce layout-preserving markdown.
- Write the markdown to object storage and publish a message for `fields-extractor`.

## Internal design

For the MVP (clean text PDFs) both steps are done with a single pdfplumber call.

When scan processing is added, the module becomes an internal sub-pipeline (e.g. OCR -> layout analysis -> text assembly). The module's external contract (PDF in, markdown out) must not change.

## Bounds (MVP)

Clean, text-based PDFs only. No OCR.

## Interfaces

- Input: queue message referencing a PDF (see `/specs/pipeline-contracts.md`).
- Output: markdown object + queue message (see `/specs/pipeline-contracts.md`).
- Markdown format: see [`output-format.md`](output-format.md).

## Open questions

- OPEN: behaviour on non-text PDFs (reject, or fail with a specific error).
- OPEN: handling of multi-page invoices.
