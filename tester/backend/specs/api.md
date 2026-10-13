# Tester Backend: HTTP API

Status: stub, to be written.

## PROPOSED endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/datasets` | List datasets (subdirectories of the datasets root) with manifest summary |
| POST | `/runs` | Start a run (dataset, parallelism) |
| GET | `/runs` | Run history |
| GET | `/runs/{id}` | Run summary and metrics |
| GET | `/runs/{id}/documents` | Per-document results, filterable by status |
| GET | `/runs/{id}/documents/{doc}` | Failure details: expected vs. actual |
| GET | `/runs/{id}/documents/{doc}/file` | Download the source file |
