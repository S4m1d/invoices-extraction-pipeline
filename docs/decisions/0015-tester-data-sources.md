# 0015. Tester data sources: YAML-configured, synthetic and document-only kinds

- Status: accepted
- Date: 2026-10-22

## Context

The tester reads datasets from one root, the `tester/datasets` symlink (0006), and every dataset must be a docsynth run with a golden set. The author now wants to run real invoices too: plain PDFs with no golden set, kept outside the repository and possibly in several places.

## Decision

- Data sources are listed in a YAML file: per source a `name`, a `kind` and a `connection` (`type` and `path`). Only `type: local` (a directory or a symlink to one) is implemented; other types fail explicitly at startup.
- Two kinds: `synthetic` (docsynth runs, compared with the golden set as before) and `documents` (plain PDFs). In both, subdirectories are datasets; loose PDFs in a `documents` source form one dataset named after the source.
- `documents` runs are judged without a golden set: a document fails when a pipeline step fails, a required field is missing (list configurable per source), or an arithmetic check fails (lines sum to subtotal, subtotal + tax = total, quantity x unit price = amount).
- For Docker, the YAML holds host paths and a Compose override mounting each source at `/sources/{name}` is generated from it by the tester image (`tester sources-override`) on `make up`.

Details: `tester/backend/specs/data-sources.md`, `tester/backend/specs/validation.md`.

## Consequences

- Supersedes 0006: the datasets root becomes one `synthetic` source; `DATASETS_DIR` is replaced by `infra/tester-sources.yaml`. `tester/datasets` remains as a symlink for module tests.
- Adding a source is a YAML edit and `make up`; no code or Compose edits.
- Real invoices pass through the LLM provider and are stored in the pipeline and tester databases like any upload.
- Passing a `documents` run means "complete and self-consistent", not "correct": a consistent misread (for example the same wrong digit in a line and the subtotal) passes.
