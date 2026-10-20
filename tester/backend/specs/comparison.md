# Tester Backend: Comparison Rules

Status: approved.

How a document's extracted invoice (PostgreSQL) is compared with its golden record (`dataset-format.md`). Compared fields: the extraction schema's mapping (`fields-extractor/specs/extraction-schema.md`, ADR 0008); `tax_rate` is not compared.

## Field outcomes

Each compared field gets one outcome:

| Outcome | Extracted | Golden |
|---|---|---|
| `match` | value | equal value (rules below), or both null |
| `mismatch` | value | different value |
| `missing` | null | value |
| `unexpected` | value | null |

## Value rules

| Kind | Fields | Rule |
|---|---|---|
| Text | `supplier_name`, `recipient_name`, `invoice_number`, line `description` | Equal after trimming and collapsing runs of whitespace to one space (Unicode NFC). Case-sensitive. On a 10-invoice real-LLM sample, all 88 header values matched exactly without any normalisation. |
| Date | `issue_date`, `due_date` | Equal dates |
| Currency | `currency` | Equal codes |
| Money, quantity | `subtotal`, `tax_total`, `grand_total`, line `amount`, `quantity` | Equal decimals (`1.0` = `1.00`) |
| Unit price | line `unit_price` | Equal decimals after rounding the golden value (half up) to the decimal places of the extracted value. Golden has 4 decimals where invoices print 2 (`0.1290` vs printed `0.13`); on the sample this turned 5 of 53 mismatches into matches. |

## Lines

Golden lines are the top-level ones (`parent_line_no` null), in printed order (below); extracted lines are ordered by their `line_no`. The *i*-th extracted line is compared with the *i*-th golden line: `description`, `quantity`, `unit_price`, `amount` (field outcomes as above). If the counts differ, the remaining golden lines are `missing` and the remaining extracted lines are `extra`.

**Printed order.** Golden `line_no` is generation order. Telecom invoices print their lines grouped: by `group_key` (the subscriber's phone number), then by `section` (Messaging, Voice, Data usage), so `line_no` order differs from what is printed. Golden lines are therefore ordered by the smallest `line_no` of their `group_key`, then the smallest `line_no` of their `section` within that group, then `line_no`. Lines with null `group_key` and `section` (all non-telecom invoices in run-001) keep `line_no` order. Checked on run-001: on `inv_00000000` the order equals the PDF's 318 printed lines; on the 12 telecom invoices of a real-LLM run, quantity and amount matched on every line in this order (3 of 318 by `line_no` order on `inv_00000000`).

**Unit price on telecom invoices.** Telecom invoices (golden `business_type` `telecom`) print no unit price, while the golden set has one for every line. On these invoices a null extracted `unit_price` is not compared (no outcome); an extracted value is compared as usual.

## Document status

| Status | When |
|---|---|
| `passed` | Every header field and every line field is `match`, and there are no `missing` or `extra` lines |
| `failed` | Otherwise: any `mismatch`, `missing` or `unexpected` field, or any `missing` or `extra` line |
| `error` | The pipeline reported a failure (`documents.failed`) or the upload failed |
| `timeout` | Not finished in time (`overview.md`, Completion) |

A `missing` field fails the document, also where the value is not printed on the invoice (except telecom `unit_price`, above). Known case in run-001: `recipient_name` and `due_date` on the `receipt-compact-01` template.

## Expected ceiling on run-001

With these rules, all `receipt-compact-01` invoices (24) fail even when the extraction is correct, so the best possible pass rate on run-001 is 88 % (176 of 200). The field accuracy table in the run summary shows which fields cause failures; `missing` on this template is expected.
