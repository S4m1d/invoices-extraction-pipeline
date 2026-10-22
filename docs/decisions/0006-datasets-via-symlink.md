# 0006. Datasets supplied through a symlinked directory

- Status: superseded by 0015
- Date: 2026-10-13

## Context

Datasets are generated manually by docsynth and are large, external data. The draft assumed a single `.db` golden set; actual docsynth output is a directory per run with PDFs, manifests and gzip JSON Lines golden shards.

## Decision

- The author provides a datasets root directory and links it into the repo as `tester/datasets`. First target: `/home/dev/docsynth/invoice-data-extractor-corpus/blobs`.
- Each subdirectory of the root is one dataset (e.g. `run-001`) in the docsynth layout described in `tester/backend/specs/dataset-format.md`. This is the standard format for supplying datasets.
- The testing client discovers datasets by scanning the root and lets the user switch between them.
- Datasets are read-only for the client; DuckDB reads the golden shards.

## Consequences

- No dataset data is stored in the repository.
- Docker setups must mount the real target path (symlinks do not resolve across bind mounts).
- Golden set field names differ from pipeline field names; the mapping must be specified (open question in the dataset format spec).
