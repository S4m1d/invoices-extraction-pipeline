//go:build integration

package db

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"testing"

	"github.com/google/uuid"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/extraction"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/testdata"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/testutil"
)

var quiet = slog.New(slog.DiscardHandler)

func open(t *testing.T, url string) *Store {
	t.Helper()
	s, err := Open(context.Background(), url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	return s
}

func migrated(t *testing.T) (*Store, string) {
	t.Helper()
	url := testutil.TempDatabase(t, "")
	s := open(t, url)
	if err := s.Migrate(context.Background(), quiet); err != nil {
		t.Fatal(err)
	}
	return s, url
}

func record(t *testing.T) Record {
	t.Helper()
	inv, err := extraction.Decode(testdata.Example())
	if err != nil {
		t.Fatal(err)
	}
	m, p := "claude-haiku-5-5", "v3"
	return Record{DocumentID: uuid.NewString(), CorrelationID: uuid.NewString(), ExtractionModel: &m, PromptVersion: &p, Invoice: inv}
}

func ptr(s string) *string { return &s }

func TestMigrateIsRepeatableAndGrants(t *testing.T) {
	s, url := migrated(t)
	if err := s.Migrate(context.Background(), quiet); err != nil {
		t.Fatalf("second run: %v", err)
	}
	admin := testutil.AdminConn(t, url)
	var versions int
	admin.QueryRow(context.Background(), "SELECT count(*) FROM goose_db_version WHERE version_id > 0").Scan(&versions)
	if versions != 2 {
		t.Fatalf("%d migrations recorded, want 2", versions)
	}
	var canRead, canWrite bool
	admin.QueryRow(context.Background(), `SELECT has_table_privilege('tester', 'invoices', 'SELECT'),
		has_table_privilege('tester', 'invoices', 'INSERT')`).Scan(&canRead, &canWrite)
	if !canRead || canWrite {
		t.Fatalf("tester read=%v write=%v, want read only", canRead, canWrite)
	}
}

func TestParallelMigrations(t *testing.T) {
	url := testutil.TempDatabase(t, "")
	var wg sync.WaitGroup
	errs := make([]error, 3)
	for i := range errs {
		wg.Add(1)
		go func() {
			defer wg.Done()
			errs[i] = open(t, url).Migrate(context.Background(), quiet)
		}()
	}
	wg.Wait()
	for i, err := range errs {
		if err != nil {
			t.Fatalf("replica %d: %v", i, err)
		}
	}
}

func TestWriteStoresExactValues(t *testing.T) {
	s, url := migrated(t)
	r := record(t)
	r.Invoice.Lines[0].UnitPrice = ptr("0.0025")
	r.Invoice.Lines[1].Amount = "-5.00"
	r.Invoice.Lines[2].Quantity = nil
	r.Invoice.GrandTotal = "123456789012345678.123456"
	r.Invoice.RecipientName, r.Invoice.DueDate, r.PromptVersion = nil, nil, nil
	if err := s.Write(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	admin := testutil.AdminConn(t, url)
	var grand, issue string
	var recipient, due, prompt *string
	admin.QueryRow(context.Background(), `SELECT grand_total::text, issue_date::text, recipient_name, due_date::text, prompt_version
		FROM invoices WHERE document_id = $1`, r.DocumentID).Scan(&grand, &issue, &recipient, &due, &prompt)
	if grand != "123456789012345678.123456" || issue != "2025-07-25" || recipient != nil || due != nil || prompt != nil {
		t.Fatalf("invoice: %s %s %v %v %v", grand, issue, recipient, due, prompt)
	}
	rows, _ := admin.Query(context.Background(), `SELECT l.line_no, l.quantity::text, l.unit_price::text, l.amount::text
		FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id WHERE i.document_id = $1 ORDER BY l.line_no`, r.DocumentID)
	var got []string
	for rows.Next() {
		var n int
		var q, u *string
		var a string
		rows.Scan(&n, &q, &u, &a)
		got = append(got, fmtp(q)+"|"+fmtp(u)+"|"+a)
	}
	want := []string{"1|0.0025|268.21", "6|30.27|-5.00", "null|324.22|324.22"}
	if len(got) != 3 || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] {
		t.Fatalf("lines %v, want %v", got, want)
	}
}

func fmtp(s *string) string {
	if s == nil {
		return "null"
	}
	return *s
}

func TestWriteIsAtomic(t *testing.T) {
	s, url := migrated(t)
	r := record(t)
	r.Invoice.Lines[2].Amount = "12,3" // invalid numeric in the last line
	err := s.Write(context.Background(), r)
	if !errors.Is(err, ErrRejected) {
		t.Fatalf("err %v, want ErrRejected", err)
	}
	var n int
	testutil.AdminConn(t, url).QueryRow(context.Background(), "SELECT count(*) FROM invoices").Scan(&n)
	if n != 0 {
		t.Fatalf("%d invoices stored after a failed write", n)
	}
}

func TestRejectedValues(t *testing.T) {
	s, _ := migrated(t)
	for name, mutate := range map[string]func(r *Record){
		"bad date":       func(r *Record) { r.Invoice.IssueDate = "2025-02-30" },
		"bad currency":   func(r *Record) { r.Invoice.Currency = "gbp" },
		"line_no 0":      func(r *Record) { r.Invoice.Lines[0].LineNo = 0 },
		"duplicate line": func(r *Record) { r.Invoice.Lines[1].LineNo = 1 },
		"bad decimal":    func(r *Record) { r.Invoice.Subtotal = ptr("$5") },
	} {
		t.Run(name, func(t *testing.T) {
			r := record(t)
			mutate(&r)
			if err := s.Write(context.Background(), r); !errors.Is(err, ErrRejected) {
				t.Fatalf("err %v, want ErrRejected", err)
			}
		})
	}
}

func TestDuplicate(t *testing.T) {
	s, _ := migrated(t)
	r := record(t)
	if err := s.Write(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	r2 := record(t)
	r2.DocumentID = r.DocumentID
	if err := s.Write(context.Background(), r2); !errors.Is(err, ErrDuplicate) {
		t.Fatalf("err %v, want ErrDuplicate", err)
	}
}

func TestConcurrentDuplicate(t *testing.T) {
	s, url := migrated(t)
	r := record(t)
	errs := make([]error, 4)
	var wg sync.WaitGroup
	for i := range errs {
		wg.Add(1)
		go func() {
			defer wg.Done()
			errs[i] = s.Write(context.Background(), r)
		}()
	}
	wg.Wait()
	ok, dup := 0, 0
	for _, err := range errs {
		switch {
		case err == nil:
			ok++
		case errors.Is(err, ErrDuplicate):
			dup++
		default:
			t.Fatalf("unexpected error %v", err)
		}
	}
	var rows int
	testutil.AdminConn(t, url).QueryRow(context.Background(), "SELECT count(*) FROM invoices").Scan(&rows)
	if ok != 1 || dup != 3 || rows != 1 {
		t.Fatalf("ok=%d duplicates=%d rows=%d", ok, dup, rows)
	}
}

func TestUnavailable(t *testing.T) {
	proxy := testutil.NewProxy(t, testutil.PostgresTarget(t))
	url := testutil.TempDatabase(t, proxy.Addr)
	s := open(t, url)
	if err := s.Migrate(context.Background(), quiet); err != nil {
		t.Fatal(err)
	}
	if err := s.Ping(context.Background()); err != nil {
		t.Fatal(err)
	}
	proxy.Down()
	if err := s.Write(context.Background(), record(t)); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("err %v, want ErrUnavailable", err)
	}
	if err := s.Ping(context.Background()); err == nil {
		t.Fatal("ping should fail while down")
	}
	proxy.Up(t, "")
	if err := s.Write(context.Background(), record(t)); err != nil {
		t.Fatalf("after recovery: %v", err)
	}
}
