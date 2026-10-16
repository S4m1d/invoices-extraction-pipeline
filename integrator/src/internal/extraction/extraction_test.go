package extraction

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/testdata"
)

func mutate(t *testing.T, f func(m map[string]any)) []byte {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(testdata.Example(), &m); err != nil {
		t.Fatal(err)
	}
	f(m)
	b, _ := json.Marshal(m)
	return b
}

func line0(m map[string]any) map[string]any { return m["lines"].([]any)[0].(map[string]any) }

func TestDecodesSpecExample(t *testing.T) {
	inv, err := Decode(testdata.Example())
	if err != nil {
		t.Fatal(err)
	}
	if inv.SupplierName != "Willowmere Halcyon Ltd" || inv.GrandTotal != "928.86" || inv.Currency != "GBP" ||
		inv.IssueDate != "2025-07-25" || *inv.DueDate != "2025-08-24" || *inv.TaxRate != "20" {
		t.Fatalf("header %+v", inv)
	}
	if len(inv.Lines) != 3 || inv.Lines[1].LineNo != 2 || inv.Lines[1].Description != "Additional user seats" ||
		*inv.Lines[1].Quantity != "6" || *inv.Lines[1].UnitPrice != "30.27" || inv.Lines[1].Amount != "181.62" {
		t.Fatalf("lines %+v", inv.Lines)
	}
}

func TestNullableFieldsAcceptNull(t *testing.T) {
	inv, err := Decode(mutate(t, func(m map[string]any) {
		for _, k := range []string{"recipient_name", "due_date", "subtotal", "tax_total", "tax_rate"} {
			m[k] = nil
		}
		line0(m)["quantity"], line0(m)["unit_price"] = nil, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	if inv.RecipientName != nil || inv.DueDate != nil || inv.Lines[0].Quantity != nil || inv.Lines[0].UnitPrice != nil {
		t.Fatalf("nulls not kept: %+v", inv)
	}
}

func TestRejected(t *testing.T) {
	cases := map[string]struct {
		data []byte
		want string
	}{
		"not JSON":             {[]byte("nope"), "not a JSON object"},
		"array":                {[]byte("[1]"), "not a JSON object"},
		"missing field":        {mutate(t, func(m map[string]any) { delete(m, "currency") }), "currency: missing"},
		"unknown field":        {mutate(t, func(m map[string]any) { m["sku"] = "x" }), "unknown fields: sku"},
		"wrong type":           {mutate(t, func(m map[string]any) { m["grand_total"] = 928.86 }), "grand_total"},
		"null required":        {mutate(t, func(m map[string]any) { m["supplier_name"] = nil }), "supplier_name: required, got null"},
		"empty lines":          {mutate(t, func(m map[string]any) { m["lines"] = []any{} }), "at least one line"},
		"null lines":           {mutate(t, func(m map[string]any) { m["lines"] = nil }), "lines"},
		"line missing field":   {mutate(t, func(m map[string]any) { delete(line0(m), "amount") }), "lines.0.amount: missing"},
		"line unknown field":   {mutate(t, func(m map[string]any) { line0(m)["sku"] = "x" }), "lines.0.unknown fields: sku"},
		"line null required":   {mutate(t, func(m map[string]any) { line0(m)["description"] = nil }), "lines.0.description: required"},
		"line_no not a number": {mutate(t, func(m map[string]any) { line0(m)["line_no"] = "1" }), "line_no"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := Decode(c.data)
			if !errors.Is(err, ErrInvalid) {
				t.Fatalf("err %v, want ErrInvalid", err)
			}
			if !strings.Contains(err.Error(), c.want) {
				t.Fatalf("err %q, want it to mention %q", err, c.want)
			}
		})
	}
}

func TestValuesAreNotValidated(t *testing.T) {
	// ADR 0012: the content is trusted; PostgreSQL checks decimals and dates.
	_, err := Decode(mutate(t, func(m map[string]any) { m["grand_total"], m["issue_date"] = "12,3", "yesterday" }))
	if err != nil {
		t.Fatalf("decoding should not validate values: %v", err)
	}
}
