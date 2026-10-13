# 0008. Extraction schema: core header + basic lines, invoice-level tax

- Status: accepted
- Date: 2026-10-13

## Context

The draft named "amount, tax rate, item description, quantity, supplier name, dates, etc". The golden sets from docsynth have richer fields but no `tax_rate`; they have `tax_total`, `tax_bucket_count` and per-line `tax_code`.

## Decision

- Invoice header: `supplier_name`, `recipient_name`, `invoice_number`, `issue_date`, `due_date`, `currency`, `subtotal`, `tax_total`, `grand_total`, plus optional `tax_rate`.
- Line: `line_no`, `description`, `quantity`, `unit_price`, `amount`.
- Tax is invoice-level. `tax_rate` is optional and not evaluated (no ground truth).
- Required: `supplier_name`, `invoice_number`, `issue_date`, `currency`, `grand_total`, at least one line with `description` and `amount`. Everything else is nullable.
- A required field missing or failed validation after retries marks the document failed; no partial insert.

## Consequences

- Every extracted field except `tax_rate` can be evaluated against golden (mapping in the schema spec).
- Discounts, shipping, deposit, SKUs and tax codes are out of scope until a later ADR extends the schema.
