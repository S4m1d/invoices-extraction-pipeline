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

`quantity` and `unit_price` are nullable; `description` and `amount` are required.

## Rules

- Money and quantity values are decimals, never floats. In `extracted.json` they are JSON strings in plain decimal notation (`"1234.56"`, `"-5.00"`, `"0.0025"`): no currency symbols, no thousands separators, `.` as decimal separator, as many decimal places as printed. Consumers parse them as decimals.
- Dates are `YYYY-MM-DD` strings.
- `lines` are the invoice's top-level line items only, in printed order (`line_no` 1..N without gaps). Sub-rows that break a line down (usage tiers, `7 included · 6 additional`, billing periods) are not lines; their content may be reflected in the parent's `description` only if printed on the same row. In run-001, top-level line amounts sum to `subtotal` on all 200 invoices and tier rows (111 rows in 23 invoices) share their parent's golden `line_no`.
- `description` is the item text without a leading item code (SKU, part or plan number) printed before a dash: `SKU-36747 — Premium support add-on` → `Premium support add-on`. Item codes are out of scope for the MVP, and golden descriptions do not contain them.
- `currency` is the ISO 4217 code (3 uppercase letters), one per invoice; all amounts of the invoice are in it. No run-001 invoice prints a code: `£` means `GBP`, while `$` is `USD` or `CAD` and must be inferred from context (Canadian addresses, GST/HST/PST, Canadian registration numbers → `CAD`). If amounts appear in more than one currency, the currency of the totals is used.
- Fields not in the tables above (discounts, shipping, deposit, tax codes, SKUs, `kind`) are out of scope for the MVP.
- Optional fields that cannot be found are `null`.
- If a required field is missing or the output fails validation, the extraction is retried; after the retry limit the document is marked failed and nothing is inserted (no partial invoices). Retry limit and validation rules: `overview.md` (Validation).
- `tax_rate` is optional, extracted only when printed on the invoice; it has no golden ground truth and is excluded from evaluation.

## Example

```json
{
  "supplier_name": "Willowmere Halcyon Ltd",
  "recipient_name": "Vireo Labs",
  "invoice_number": "INV-2025-000029",
  "issue_date": "2025-07-25",
  "due_date": "2025-08-24",
  "currency": "GBP",
  "subtotal": "774.05",
  "tax_total": "154.81",
  "grand_total": "928.86",
  "tax_rate": "20",
  "lines": [
    {"line_no": 1, "description": "Premium support add-on", "quantity": "1", "unit_price": "268.21", "amount": "268.21"},
    {"line_no": 2, "description": "Additional user seats", "quantity": "6", "unit_price": "30.27", "amount": "181.62"},
    {"line_no": 3, "description": "Growth plan — monthly subscription", "quantity": "1", "unit_price": "324.22", "amount": "324.22"}
  ]
}
```

(`inv_00000029`; see `text-extractor/specs/output-format.md` for its markdown. All values match the golden set.) `tax_rate` is a percentage (`"20"` for 20 %).

## Notes for evaluation (tester)

- Golden `unit_price` has 4 decimal places on most rows while invoices print 2 (for example a golden `0.0025` printed as `$0.00`). Comparison rules are owned by the tester spec.
- In telecom invoices (`telecom-itemized-37`) golden `line_no` order differs from printed order (lines are printed grouped by category), so lines cannot be matched by `line_no` alone.

## Open questions

None.
