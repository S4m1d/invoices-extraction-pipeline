# AGENTS.md

Instructions for AI coding agents working in this repository.

## Project

Invoice data extraction pipeline (study project, Spec-Driven Development). Overview: [`README.md`](README.md). Current stage: skeleton and specs; implementation happens module by module after its spec is approved.

## Read first

1. [`README.md`](README.md): architecture and repository layout.
2. [`specs/pipeline-contracts.md`](specs/pipeline-contracts.md): queues, message envelope, object storage keys.
3. The `specs/` directory of the module you are working on.
4. [`docs/decisions/`](docs/decisions/): why things are the way they are.

## Rules

- **Specs are the source of truth.** If code and spec disagree, the spec wins. If a spec is wrong or incomplete, propose a spec change first; do not silently diverge.
- **Do not guess.** Items marked `OPEN:` are undecided: ask the author instead of choosing. Items marked `PROPOSED:` are suggestions not yet confirmed: implement only after confirmation.
- **Stay inside the module** you were asked to work on. Cross-module interfaces change only through `specs/pipeline-contracts.md` (and the affected module specs together).
- **Record decisions.** A significant architectural choice gets a new ADR in `docs/decisions/` (see its README).
- **Do not modify** anything under `tester/datasets/` (read-only external data, symlink).
- Keep the root `README.md` general; technical details (tables, APIs, formats) belong in module `specs/`.

## Modules

| Module | Path | Stack | Spec |
|---|---|---|---|
| Ingestor | `ingestor/` | Go | `ingestor/specs/` |
| Text extractor | `text-extractor/` | Python, pdfplumber | `text-extractor/specs/` |
| Fields extractor | `fields-extractor/` | Python, Pydantic, Anthropic SDK (provider abstraction) | `fields-extractor/specs/` |
| Integrator | `integrator/` | Go, PostgreSQL | `integrator/specs/` |
| Tester backend | `tester/backend/` | Rust, DuckDB, SQLite, PostgreSQL | `tester/backend/specs/` |
| Tester frontend | `tester/frontend/` | JS, React | `tester/frontend/specs/` |
| Infrastructure | `infra/` | Docker | `infra/README.md` |

Each module keeps its code in `src/` and its specification in `specs/`.

## Constraints to remember

- Services communicate only through RabbitMQ; files live in MinIO; messages carry references, not file bytes.
- Everything runs in Docker containers.
- MVP input is clean, text-based PDFs; no OCR yet. Do not add scan handling outside `text-extractor`.
- LLM access goes through the provider abstraction; only Anthropic is implemented, other providers must fail explicitly.
- Test datasets follow [`tester/backend/specs/dataset-format.md`](tester/backend/specs/dataset-format.md).
