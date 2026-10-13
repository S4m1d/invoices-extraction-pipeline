# Fields Extractor: Extraction Schema

JSON produced for one invoice. One invoice has many lines. Must stay aligned with `integrator/specs/data-model.md`. Decision record: `docs/decisions/0008-extraction-schema.md`.

## Invoice

| Field | Type | Required | Golden source (`golden/invoices`) |
|---|---|---|---|
| `supplier_name` | string | yes | `issuer_name` |
| `recipient_name` | string | no | `recipient_name` |
| `invoice_number` | string | yes | `invoice_number` |
| `issue_date` | date (`YYYY-MM-DD`) | yes | `issue_date` |
| `due_date` | date | no | `due_date` |
| `currency` | string (ISO 4217) | yes | `currency` |
| `subtotal` | decimal | no | `subtotal` |
| `tax_total` | decimal | no | `tax_total` |
| `grand_total` | decimal | yes | `grand_total` |
| `tax_rate` | decimal | no | none (not evaluated) |
| `lines` | array of Line, at least 1 | yes | see below |

## Line

| Field | Type | Required | Golden source (`golden/line_items`) |
|---|---|---|---|
| `line_no` | int, 1-based, document order | yes | `line_no` |
| `description` | string | yes | `description` |
| `quantity` | decimal | no | `quantity` |
| `unit_price` | decimal | no | `unit_price` |
| `amount` | decimal | yes | `extended_amount` |

PROPOSED: `quantity` and `unit_price` are nullable; `description` and `amount` are required.

## Rules

- Money and quantity values are decimals, never floats.
- Fields not in the tables above (discounts, shipping, deposit, tax codes, SKUs, `kind`) are out of scope for the MVP.
- Optional fields that cannot be found are `null`.
- If a required field is missing or the output fails validation, the extraction is retried; after the retry limit the document is marked failed and nothing is inserted (no partial invoices).
- `tax_rate` is optional, extracted only when printed on the invoice; it has no golden ground truth and is excluded from evaluation.

## Open questions

- OPEN: retry limit (see `overview.md`).
- OPEN: how a failed document is reported (see `/specs/pipeline-contracts.md`, error handling).
