# Text Extractor: Output Format

Status: approved.

The output is `documents/{document_id}/layout.md`: UTF-8 markdown, content type `text/markdown; charset=utf-8`. It is read by `fields-extractor` (LLM input) and by people debugging a document.

## Why layout text, not markdown tables

pdfplumber's text in layout mode keeps the visual arrangement: columns stay apart, table columns stay aligned, indented sub-rows (for example usage tiers under a parent line) stay indented. Converting tables to markdown tables needs reliable table detection, and on run-001 it is not reliable: a zebra-striped table was detected as one row, a borderless table not at all. Plain text extraction (non-layout) merges side-by-side columns into one line (`Willowmere Halcyon Ltd DUE 24/08/2025`). So each page is emitted as layout text in a fenced code block, which keeps its whitespace intact.

## Structure

````
<!-- document_id: {document_id}; pages: {page_count} -->

## Page 1 of N

```text
...layout text of page 1...
```

## Page 2 of N
...
````

- First line: an HTML comment with `document_id` and page count (for debugging; consumers must not depend on it). If any page was OCRed, it also has `ocr_pages` with their numbers: `<!-- document_id: {document_id}; pages: 3; ocr_pages: 2,3 -->`.
- One section per page, in document order, headed `## Page {i} of {N}`. Pages whose text comes from OCR are headed `## Page {i} of {N} (OCR)` (see [`ocr.md`](ocr.md)); their text may contain recognition errors.
- Page text sits in a fenced block with info string `text`. If the page text contains a run of backticks as long as the fence, the fence is lengthened (```` ```` ````, and so on) so the block cannot end early.
- Pages without any text are kept as a section with an empty block, so page numbers stay aligned with the PDF.

## Text normalization

- Characters with font size of 72 pt or more are removed before extraction. These are watermarks: in run-001 normal text is at most 30 pt and watermark letters at least 226 pt, and this rule matches the golden `has_watermark` flag on all 200 documents. Without it, single watermark letters appear inside table rows.
- Trailing whitespace is removed from every line.
- Runs of blank lines are collapsed to one blank line; leading and trailing blank lines of a page are removed.
- The common left indentation of the page is removed. Spacing inside lines is kept as extracted.
- Running headers and footers (for example `... Invoice No. ... Page 2 of 4`) are kept as they are. Removing them is not needed for extraction and would risk dropping real content.
- These rules apply to OCRed pages too, after layout assembly (see [`ocr.md`](ocr.md)).
- Text is not otherwise changed: no spelling, number, date or currency normalization (that is `fields-extractor`'s job).

## Example

Input: `run-001/documents/unit-000000/inv_00000029.pdf` (template `meta-sidebar-01`, zebra table, one page). Output:

````markdown
<!-- document_id: 0f8fad5b-d9cb-469f-a165-70867728950e; pages: 1 -->

## Page 1 of 1

```text
Willowmere Halcyon Ltd — Invoice No. INV-2025-000029               Page 1 of 1

                                     INVOICE NO. INV-2025-000029
                                     DATE      25/07/2025

Willowmere   Halcyon  Ltd            DUE       24/08/2025
                                     Bill To
Invoice                              Vireo Labs

8641 Ironclad Street
Bristol
+1 283-555-7473
billing@willowmerehalcyonltd.example
www.willowmerehalcyonltd.example

 Item Description                               QTY      Price    Amount

 SKU-36747 — Premium support add-on               1    £268.21   £268.21
 SKU-46259 — Additional user seats                6     £30.27   £181.62
 7 included · 6 additional

 PLN-45945 — Growth plan — monthly subscription   1    £324.22   £324.22
 21/06/2025 – 20/07/2025

                                              Subtotal           £774.05

                                              VAT (20%)          £154.81

                                              Total              £928.86

Registration Numbers — VAT : GB 106 6919 15

Thank you for your business!
```
````

## Size

On run-001 the largest output is 34 KB (`inv_00000131`, 395 lines, 11 pages), about 10k LLM tokens. No size limit is applied.
