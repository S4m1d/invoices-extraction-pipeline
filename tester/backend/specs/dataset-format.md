# Tester: Dataset Format

Datasets are produced manually by docsynth and supplied to the testing client by the author through a symlink. This layout is the standard format for supplying datasets.

## Supplying datasets

- A directory of docsynth runs is configured as a data source of kind `synthetic` ([`data-sources.md`](data-sources.md)); there can be several. `tester/datasets` stays a symlink to the main one (`/home/dev/docsynth/invoice-data-extractor-corpus/blobs`), used by module tests such as `text-extractor`'s corpus tests.
- Every immediate subdirectory of such a source is one dataset (`run-001`, `run002`, ...). The dataset name is the directory name.
- The testing client lists datasets by scanning the sources and lets the user switch between them. New dataset directories show up without code changes.
- Datasets are read-only for the testing client.
- Plain PDFs without a golden set use a `documents` source instead ([`data-sources.md`](data-sources.md)); this document's format does not apply to them.

## Dataset directory layout

```
<datasets-root>/
└── <run_id>/                                   e.g. run-001
    ├── manifest.json                           run-level manifest
    ├── manifest/
    │   └── unit-{unit:06d}.json                per-unit document list
    ├── documents/
    │   └── unit-{unit:06d}/
    │       └── {invoice_id}.pdf                e.g. inv_00000000.pdf
    └── golden/
        ├── invoices/
        │   └── unit-{unit:06d}.jsonl.gz        one JSON object per invoice
        └── line_items/
            └── unit-{unit:06d}.jsonl.gz        one JSON object per invoice line
```

A *unit* is a shard of the dataset; a dataset has `total_units` units (run-001 has one: `unit-000000`). Readers must handle multiple units.

## `manifest.json`

| Field | Example (run-001) | Notes |
|---|---|---|
| `run_id` | `run-001` | Same as directory name |
| `pack` | `invoice` | |
| `schema_version` | `1` | Reader must check |
| `catalogue_version` | `seed-1` | |
| `config_id` | `default` | |
| `created_at`, `completed_at` | RFC 3339 | |
| `total_documents` | `200` | |
| `total_units` | `1` | |
| `table_rows` | `{"invoices": 200, "line_items": 14392}` | Row counts per golden table |
| `document_key_pattern` | `{run_id}/documents/unit-{unit:06d}/{record_id}{ext}` | Keys are relative to the source directory |
| `shard_key_pattern` | `{run_id}/golden/{table}/unit-{unit:06d}.jsonl.gz` | Keys are relative to the source directory |
| `parts[]` | `{unit_index, start_index, count, documents, bytes, sha256, key}` | One per unit; `key` points to the unit manifest |

## `manifest/unit-{unit:06d}.json`

```json
{ "count": 200,
  "documents": [ { "index": 0, "key": "run-001/documents/unit-000000/inv_00000000.pdf", "bytes": 124290, "sha256": "..." } ] }
```

`sha256` can be used to verify file integrity before a run.

## Golden tables

Both are gzip-compressed JSON Lines (one JSON object per line). Join key: `invoice_id`, which equals the PDF file name without extension.

### Value encoding

- Dates are encoded as `{"__date__": "YYYY-MM-DD"}`.
- Decimals are encoded as `{"__decimal__": "4976.67"}` (string, never float). Readers must parse these into native date/decimal types.
- Absent values are `null`.

### `golden/invoices`

One row per document. Fields relevant to extraction evaluation:

| Field | Type | Notes |
|---|---|---|
| `invoice_id` | string | e.g. `inv_00000000` |
| `invoice_number` | string | |
| `issue_date`, `due_date` | date | `due_date` may be null |
| `billing_period_start`, `billing_period_end` | date, null | |
| `currency` | string | `USD`, `CAD`, `GBP` in run-001 |
| `issuer_name` | string | The supplier |
| `recipient_name` | string | |
| `subtotal`, `discount_total`, `shipping_total`, `taxable_base`, `tax_total`, `deposit`, `grand_total` | decimal | |
| `tax_bucket_count`, `line_item_count`, `printed_row_count` | int | |

Metadata fields describing how the document was generated (usable for filtering and for analytics on failures):

`run_id`, `invoice_index`, `company_id`, `business_type`, `product_category`, `llm_niche`, `template_id`, `locale`, `language`, `jurisdiction`, `discount_scheme`, `discount_timing`, `billing_models`, `condition` (`clean` for all of run-001), `is_handwritten`, `is_degraded`, `wear`, `is_crisp`, `goods_receipt`, `received_date`, `page_count`, `meta_position`, `totals_style`, `table_style`, `column_vocabulary`, `typeface`, `font_scale`, `has_logo`, `logo_style`, `has_watermark`, `catalogue_version`, `gcs_pdf_path`, `gcs_golden_shard`.

### `golden/line_items`

Many rows per invoice, ordered by `line_no`.

| Field | Type | Notes |
|---|---|---|
| `invoice_id` | string | FK to `golden/invoices` |
| `line_no` | int | 1-based |
| `parent_line_no` | int, null | |
| `kind` | string | `usage`, `product`, `subscription`, `fee`, `service`, `seat`, `labour`, `part`, `surcharge` |
| `description` | string | |
| `quantity`, `unit_price`, `discount_amount`, `extended_amount` | decimal | |
| `discount_percent` | number, null | |
| `tax_code`, `sku`, `part_number`, `code`, `code_system` | string, null | |
| `is_credit` | bool | |

Remaining fields (tiering, proration, usage details, grouping: `tier_*`, `period_*`, `is_prorated`, `proration_factor`, `included_quantity`, `overage_quantity`, `usage_*`, `duration_seconds`, `volume`, `usage_type`, `counterparty`, `rate_per_unit`, `group_key`, `group_label`, `section`, `billing_model`, `run_id`) are generation metadata.

## Notes for run-001

- 200 documents, all `clean`, none handwritten or degraded: fits the MVP bounds (clean text PDFs).
- 14,392 line items in total; a single invoice may have hundreds of lines (e.g. `inv_00000000` has 318). This matters for LLM context size in `fields-extractor`.
- Contains `fr-CA` invoices (4) and three currencies.

## Mapping to the pipeline schema

Decided in ADR 0008. Field-by-field mapping from golden to the extraction schema is in `fields-extractor/specs/extraction-schema.md` (e.g. `issuer_name` -> `supplier_name`, `extended_amount` -> `amount`). Only mapped fields are compared; `tax_rate` is not compared.

## Open questions

- Comparison rules: see [`comparison.md`](comparison.md).
