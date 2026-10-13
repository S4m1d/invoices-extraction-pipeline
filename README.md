# Invoices Data Extraction Pipeline

A study project: a pipeline that extracts structured data from invoice documents and stores it in a procurement table.

- **Input:** invoice PDFs (MVP: clean, text-based PDFs; no OCR).
- **Output:** rows in a PostgreSQL procurement table (`invoices`, `invoice_lines`).
- **Test data:** synthetic invoices and golden sets generated manually with [docsynth](https://github.com/kashodev/docsynth), supplied as dataset directories (format: [`tester/backend/specs/dataset-format.md`](tester/backend/specs/dataset-format.md)).
- **Methodology:** Spec-Driven Development (SDD). Specs are written first, code follows.

## Status

Skeleton and specs stage. No implementation yet.

## Architecture

Services are connected through RabbitMQ. Documents (PDF, markdown) are stored in object storage (MinIO/S3); queue messages carry references, not file bytes. All infrastructure runs in Docker containers.

```
client --POST--> [ingestor] --queue--> [text-extractor] --queue--> [fields-extractor] --queue--> [integrator] --> PostgreSQL
                     |                        |                              |
                     +------------- object storage (MinIO/S3) ---------------+
```

Pipeline steps: ingestion -> layout analysis + text extraction -> fields extraction -> integration.

## Repository layout

| Path | Purpose | Stack |
|---|---|---|
| `ingestor/` | REST API that accepts documents and enqueues them | Go |
| `text-extractor/` | PDF -> layout-preserving markdown | Python, pdfplumber |
| `fields-extractor/` | Markdown -> structured JSON via LLM | Python, Pydantic, Anthropic SDK |
| `integrator/` | JSON -> procurement tables | Go, PostgreSQL |
| `tester/backend/` | Test orchestration, evaluation against golden sets | Rust, DuckDB, SQLite, PostgreSQL |
| `tester/frontend/` | Web UI for test runs | JS, React |
| `infra/` | Docker Compose, RabbitMQ, MinIO, PostgreSQL setup | Docker |
| `tester/datasets` | Symlink to the directory with docsynth datasets (`run-001`, ...) | - |
| `specs/` | Cross-module contracts (queues, storage, message formats) | - |
| `docs/decisions/` | Architecture decision records (ADRs) | - |
| `AGENTS.md` | Instructions for AI coding agents | - |

Each module has its own `specs/` directory holding its technical specification (APIs, data layouts, behaviour). The root README only gives the overall picture.

## Specs index

Cross-module:
- [`specs/pipeline-contracts.md`](specs/pipeline-contracts.md): queues, message formats, object storage layout, shared conventions.
- [`docs/decisions/`](docs/decisions/): decision log (ADRs).

Per module:
- [`ingestor/specs/`](ingestor/specs/)
- [`text-extractor/specs/`](text-extractor/specs/)
- [`fields-extractor/specs/`](fields-extractor/specs/)
- [`integrator/specs/`](integrator/specs/)
- [`tester/backend/specs/`](tester/backend/specs/) (includes the [dataset format](tester/backend/specs/dataset-format.md))
- [`tester/frontend/specs/`](tester/frontend/specs/)
- [`infra/`](infra/)

## Testing approach

Test runs send invoices from a chosen dataset to the pipeline in parallel, then compare the rows that landed in the procurement table with the golden set produced by docsynth. Datasets are supplied through the `tester/datasets` symlink; the client lets the user switch between them. Results and history are kept by the testing backend. See `tester/`.

## Conventions

- Specs are the source of truth. Code must follow them; change the spec first.
- Open questions in specs are marked `OPEN:`. Decisions not yet confirmed by the author are marked `PROPOSED:`.
- Read [`AGENTS.md`](AGENTS.md) before working on any module.
- Original author draft is kept unchanged in [`README-DRAFT.md`](README-DRAFT.md).
