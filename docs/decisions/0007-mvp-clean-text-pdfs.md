# 0007. MVP scope: clean text PDFs, no OCR

- Status: accepted
- Date: 2026-10-13

## Context

Invoice documents can be clean text PDFs, scans, or degraded/handwritten documents.

## Decision

The MVP processes only clean, text-based PDFs. No OCR. Scan processing is a later feature inside `text-extractor` (see 0003).

## Consequences

- Datasets used for MVP evaluation must be `clean` (all of `run-001` is).
- Behaviour on non-text PDFs is an open question in `text-extractor/specs/overview.md`.
