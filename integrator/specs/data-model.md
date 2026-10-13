# Integrator: Data Model (PostgreSQL)

Simple procurement layout; nothing elaborate such as a purchase order id. Field set follows `fields-extractor/specs/extraction-schema.md` (ADR 0008).

## Tables

- `invoices`
- `invoice_lines`

Relation: one `invoices` row has many `invoice_lines` rows.

## `invoices`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | uuid, PK | no | |
| `document_id` | uuid, unique | no | Links back to the ingested document |
| `supplier_name` | text | no | |
| `recipient_name` | text | yes | |
| `invoice_number` | text | no | |
| `issue_date` | date | no | |
| `due_date` | date | yes | |
| `currency` | char(3) | no | |
| `subtotal` | numeric | yes | |
| `tax_total` | numeric | yes | |
| `grand_total` | numeric | no | |
| `tax_rate` | numeric | yes | Only when printed on the invoice |
| `created_at` | timestamptz | no | |

## `invoice_lines`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | uuid, PK | no | |
| `invoice_id` | uuid, FK -> `invoices.id` | no | |
| `line_no` | int | no | Unique per invoice |
| `description` | text | no | |
| `quantity` | numeric | yes | |
| `unit_price` | numeric | yes | |
| `amount` | numeric | no | |

An invoice and all its lines are inserted in one transaction.

## Open questions

- OPEN: indexes and constraints beyond the unique keys above.
- OPEN: precision/scale of `numeric` columns (golden values have up to 4 decimal places in `unit_price`).
