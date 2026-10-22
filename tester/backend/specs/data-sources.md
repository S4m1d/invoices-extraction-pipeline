# Tester: Data Sources

Status: approved. Decision record: `docs/decisions/0015-tester-data-sources.md`.

Where the testing client finds documents to run. A **data source** is a configured location; each source has a **kind** that says whether its documents come with a golden set. A **dataset** is what a run is started on; every source holds one or more datasets.

## Kinds

| Kind | Content | Evaluation |
|---|---|---|
| `synthetic` | docsynth runs with golden sets ([`dataset-format.md`](dataset-format.md)) | Compared with the golden set ([`comparison.md`](comparison.md)) |
| `documents` | Plain PDFs, no golden set (for example real invoices) | Required fields and arithmetic checks ([`validation.md`](validation.md)) |

## Configuration file

YAML, read by the tester backend at startup (path: `TESTER_SOURCES_FILE`). In the Compose setup the file is `infra/tester-sources.yaml`, gitignored because it holds machine-specific paths; `infra/tester-sources.example.yaml` is tracked.

```yaml
sources:
  - name: synthetic
    kind: synthetic
    connection:
      type: local
      path: /home/dev/docsynth/invoice-data-extractor-corpus/blobs

  - name: real
    kind: documents
    connection:
      type: local
      path: /mnt/dev/tmp/real_docs
    required_fields:          # optional, `documents` sources only; default below
      invoice: [supplier_name, invoice_number, issue_date, currency, subtotal, tax_total, grand_total]
      line: [description, quantity, amount]
```

| Key | Required | Rules |
|---|---|---|
| `sources` | yes | List, at least one entry |
| `name` | yes | Unique; lowercase letters, digits and `-`, 1 to 40 characters. Used in the API, in URLs and as the mount directory |
| `kind` | yes | `synthetic` or `documents` |
| `connection.type` | yes | `local` only (see Connections) |
| `connection.path` | yes | Absolute path of a directory, or of a symlink to one |
| `required_fields` | no | `documents` only: lists `invoice` and `line` of extraction-schema field names (`fields-extractor/specs/extraction-schema.md`); see [`validation.md`](validation.md) |

Default `required_fields` (when the key is absent): `invoice`: `supplier_name`, `invoice_number`, `issue_date`, `currency`, `subtotal`, `tax_total`, `grand_total`; `line`: `description`, `quantity`, `amount`. `recipient_name` and `due_date` are not in the default because many real invoices do not print them (a receipt has no due date).

Errors, reported all at once and ending startup with a non-zero exit: file missing or not valid YAML; a missing or invalid key; a duplicate `name`; an unknown `kind`; an unknown field name in `required_fields`; `required_fields` on a `synthetic` source; a connection type other than `local` (the message names the type and says only `local` is implemented). A configured path that does not exist or cannot be read is **not** a startup error: the source is listed with an `unavailable` reason (logged as a warning), so one missing directory does not take the tester down.

The file is read once at startup; changing it needs a restart (`make up` does this, see Docker setup).

## Connections

Only `local` exists: files on the machine running the tester, read directly from `path`. `path` may be a directory or a symlink to one; the symlink is followed. Other connection types (for example S3) are later features; the configuration format keeps them under `connection` so they can be added without changing the other keys.

## Datasets in a source

The datasets of a source are found by scanning on every request for the dataset list, so new directories show up without a restart. Hidden entries (names starting with `.`) are skipped.

**`synthetic`**: every immediate subdirectory with a readable `manifest.json` is a dataset, named after the directory (`run-001`), in the docsynth layout ([`dataset-format.md`](dataset-format.md)). Subdirectories without a usable manifest are skipped with a warning, as today.

**`documents`**:

- Every immediate subdirectory that contains at least one PDF is a dataset, named after the subdirectory.
- PDFs lying directly in the source directory form one more dataset, named after the source (`real` in the example above). If a subdirectory has the same name as the source, the subdirectory wins and a warning is logged.
- A PDF is a regular file (or a symlink to one) whose name ends in `.pdf`, case-insensitive; subdirectories of a dataset are not scanned. Other files are ignored.
- A document's `invoice_id` is the file name without the extension (`FR_FIZ2026_948`). If two files of a dataset differ only in the extension's case, the dataset is listed with an error and cannot be run.

A dataset is identified by the pair (`source`, `dataset`); the same dataset name may appear in two sources.

## Docker setup

The tester runs in a container, so every source path must be mounted into it. `infra/tester-sources.yaml` holds **host** paths; the mounts are generated from it:

- The tester image has a subcommand `tester sources-override` that reads the YAML on stdin, validates it as above, and prints a Compose override file that mounts each source's `connection.path` read-only at `/sources/{name}` in the `tester` service, plus the YAML itself at `/config/sources.yaml`.
- `make up` and `make up-fake` build the tester image, write the override to `infra/docker-compose.sources.yml` (gitignored) and start the stack with it. They stop with a message if `infra/tester-sources.yaml` is missing.
- Inside the container `TESTER_SOURCES_MOUNT_ROOT` is `/sources`: the backend then reads source `{name}` from `/sources/{name}` instead of its `connection.path`. Outside Docker (local development) the variable is empty and paths are used as written.
- Docker resolves a symlink given as the mount source, so a path such as `tester/datasets` (a symlink) can be configured directly. Symlinks *inside* a mounted directory that point outside it are still not followed, as before (ADR 0006).

Adding a source: edit `infra/tester-sources.yaml`, run `make up`.

## Privacy

`documents` sources are meant for real invoices, which can contain personal data. They are processed like any upload: sent to the configured LLM provider, stored in MinIO and PostgreSQL, and kept in the tester's SQLite run results. Their content does not go into the repository (the YAML is gitignored; no examples from them in specs or tests).
