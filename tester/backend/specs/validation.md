# Tester Backend: Validation Without a Golden Set

Status: approved.

How a document from a `documents` source ([`data-sources.md`](data-sources.md)) is judged when there is no golden set. Documents from `synthetic` sources keep the golden comparison ([`comparison.md`](comparison.md)).

The extracted invoice (PostgreSQL row and lines, read as for the comparison) is checked in two ways: required fields must be present, and the amounts must add up.

## Required fields

Each field in the source's `required_fields` (default in `data-sources.md`) gets one outcome:

| Outcome | When |
|---|---|
| `present` | The value is not null (text: not empty after trimming) |
| `missing` | Otherwise |

Header fields are checked once; line fields once per line. Fields not listed are not checked. Fields the extraction schema already requires (for example `invoice_number`) can never be `missing` here, because `fields-extractor` fails such documents (`llm_invalid_output`); listing them is harmless.

## Arithmetic checks

Amounts are compared as decimals. A computed value is rounded half up to the decimal places of the value it is compared with, then compared for equality. A check whose inputs include a null is `not_applicable`: a null input that matters is already reported as a `missing` required field.

| Check | Rule | Inputs |
|---|---|---|
| `lines_sum` | Sum of all line `amount`s equals `subtotal` | `subtotal`, every line `amount` |
| `totals` | `subtotal` + `tax_total` equals `grand_total` | `subtotal`, `tax_total`, `grand_total` |
| `line_amount` | `quantity` × `unit_price` equals `amount`, per line | the line's `quantity`, `unit_price`, `amount` |

Outcomes: `pass`, `fail`, `not_applicable`. Each result records the expected value (computed side) and the actual value (extracted side), for example `lines_sum`: expected `254.49` (sum of lines), actual `250.00` (subtotal).

Known limits, recorded rather than worked around:

- `totals` fails on invoices with a discount, shipping charge or deposit outside the lines, because the extraction schema has no such fields and `grand_total` is the amount due.
- `line_amount` can fail when the printed unit price is itself rounded (for example `0.13` printed for `0.129`) and the quantity is large.
- `lines_sum` fails when the subtotal is printed after a line-level discount that the lines do not show.

A failed check therefore means "look at this document", not necessarily a wrong extraction; the document details show both values.

## Document status

| Status | When |
|---|---|
| `passed` | Every required field is `present` and no check is `fail` |
| `failed` | Any required field `missing`, or any check `fail` |
| `error` | The pipeline reported a failure (`documents.failed`) or the upload failed |
| `timeout` | Not finished in time (`overview.md`, Completion) |

`error` and `timeout` are decided exactly as for synthetic runs.

## Summary

The run summary of a `documents` run contains, instead of field accuracy and golden breakdowns:

- Document counts by status, as for synthetic runs.
- Required fields: per field, `present` / `missing` counts (line fields counted per line).
- Checks: per check, `pass` / `fail` / `not_applicable` counts (`line_amount` counted per line).
- Breakdown by extracted `currency` (documents and pass count), since there is no golden metadata.
- Pipeline errors, models and prompt versions, durations: as for synthetic runs.
