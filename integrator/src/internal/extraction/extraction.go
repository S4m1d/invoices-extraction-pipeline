// Package extraction decodes extracted.json (fields-extractor/specs/extraction-schema.md).
//
// The content is not validated again (integrator/specs/overview.md, Processing; ADR 0012):
// decoding checks the shape (field names, JSON types, required fields not null); values such as
// decimals and dates are checked by PostgreSQL when they are written.
package extraction

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
)

// ErrInvalid marks extracted.json that cannot be decoded (error code invalid_extraction).
var ErrInvalid = errors.New("invalid extraction")

type Invoice struct {
	SupplierName  string
	RecipientName *string
	InvoiceNumber string
	IssueDate     string
	DueDate       *string
	Currency      string
	Subtotal      *string
	TaxTotal      *string
	GrandTotal    string
	TaxRate       *string
	Lines         []Line
}

type Line struct {
	LineNo      int
	Description string
	Quantity    *string
	UnitPrice   *string
	Amount      string
}

type rawInvoice struct {
	SupplierName  *string    `json:"supplier_name"`
	RecipientName *string    `json:"recipient_name"`
	InvoiceNumber *string    `json:"invoice_number"`
	IssueDate     *string    `json:"issue_date"`
	DueDate       *string    `json:"due_date"`
	Currency      *string    `json:"currency"`
	Subtotal      *string    `json:"subtotal"`
	TaxTotal      *string    `json:"tax_total"`
	GrandTotal    *string    `json:"grand_total"`
	TaxRate       *string    `json:"tax_rate"`
	Lines         *[]rawLine `json:"lines"`
}

type rawLine struct {
	LineNo      *int    `json:"line_no"`
	Description *string `json:"description"`
	Quantity    *string `json:"quantity"`
	UnitPrice   *string `json:"unit_price"`
	Amount      *string `json:"amount"`
}

var (
	invoiceKeys = []string{"supplier_name", "recipient_name", "invoice_number", "issue_date", "due_date",
		"currency", "subtotal", "tax_total", "grand_total", "tax_rate", "lines"}
	lineKeys = []string{"line_no", "description", "quantity", "unit_price", "amount"}
)

// Decode parses extracted.json. Errors wrap ErrInvalid and name the problem.
func Decode(data []byte) (Invoice, error) {
	var keys map[string]json.RawMessage
	if err := json.Unmarshal(data, &keys); err != nil {
		return Invoice{}, invalid("not a JSON object: %v", err)
	}
	if err := sameKeys("", keys, invoiceKeys); err != nil {
		return Invoice{}, err
	}
	// Field names first (also per line), so errors name the line; then the typed decode.
	var lineKeySets []map[string]json.RawMessage
	if err := json.Unmarshal(keys["lines"], &lineKeySets); err != nil {
		return Invoice{}, invalid("lines: %v", err)
	}
	for i, lk := range lineKeySets {
		if err := sameKeys(fmt.Sprintf("lines.%d.", i), lk, lineKeys); err != nil {
			return Invoice{}, err
		}
	}
	var raw rawInvoice
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&raw); err != nil {
		return Invoice{}, invalid("%v", err)
	}

	required := map[string]*string{
		"supplier_name": raw.SupplierName, "invoice_number": raw.InvoiceNumber, "issue_date": raw.IssueDate,
		"currency": raw.Currency, "grand_total": raw.GrandTotal,
	}
	for _, name := range sortedKeys(required) {
		if required[name] == nil {
			return Invoice{}, invalid("%s: required, got null", name)
		}
	}
	if raw.Lines == nil || len(*raw.Lines) == 0 {
		return Invoice{}, invalid("lines: at least one line required")
	}

	inv := Invoice{
		SupplierName: *raw.SupplierName, RecipientName: raw.RecipientName, InvoiceNumber: *raw.InvoiceNumber,
		IssueDate: *raw.IssueDate, DueDate: raw.DueDate, Currency: *raw.Currency, Subtotal: raw.Subtotal,
		TaxTotal: raw.TaxTotal, GrandTotal: *raw.GrandTotal, TaxRate: raw.TaxRate,
	}
	for i, l := range *raw.Lines {
		switch {
		case l.LineNo == nil:
			return Invoice{}, invalid("lines.%d.line_no: required, got null", i)
		case l.Description == nil:
			return Invoice{}, invalid("lines.%d.description: required, got null", i)
		case l.Amount == nil:
			return Invoice{}, invalid("lines.%d.amount: required, got null", i)
		}
		inv.Lines = append(inv.Lines, Line{
			LineNo: *l.LineNo, Description: *l.Description, Quantity: l.Quantity, UnitPrice: l.UnitPrice,
			Amount: *l.Amount,
		})
	}
	return inv, nil
}

func sameKeys(prefix string, got map[string]json.RawMessage, want []string) error {
	wantSet := map[string]bool{}
	for _, k := range want {
		wantSet[k] = true
		if _, ok := got[k]; !ok {
			return invalid("%s%s: missing", prefix, k)
		}
	}
	var extra []string
	for k := range got {
		if !wantSet[k] {
			extra = append(extra, k)
		}
	}
	if len(extra) > 0 {
		sort.Strings(extra)
		return invalid("%sunknown fields: %s", prefix, strings.Join(extra, ", "))
	}
	return nil
}

func sortedKeys(m map[string]*string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func invalid(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrInvalid, fmt.Sprintf(format, args...))
}
