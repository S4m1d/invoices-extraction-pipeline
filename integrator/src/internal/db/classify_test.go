package db

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgconn"
)

func TestClassify(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want error
	}{
		{"duplicate document", &pgconn.PgError{Code: "23505", ConstraintName: "invoices_document_id_key"}, ErrDuplicate},
		{"other unique violation", &pgconn.PgError{Code: "23505", ConstraintName: "invoice_lines_invoice_id_line_no_key"}, ErrRejected},
		{"check violation", &pgconn.PgError{Code: "23514", ConstraintName: "invoices_currency_check"}, ErrRejected},
		{"not null", &pgconn.PgError{Code: "23502"}, ErrRejected},
		{"invalid numeric", &pgconn.PgError{Code: "22P02"}, ErrRejected},
		{"invalid date", &pgconn.PgError{Code: "22008"}, ErrRejected},
		{"connection exception", &pgconn.PgError{Code: "08006"}, ErrUnavailable},
		{"too many connections", &pgconn.PgError{Code: "53300"}, ErrUnavailable},
		{"admin shutdown", &pgconn.PgError{Code: "57P01"}, ErrUnavailable},
		{"cannot connect now", &pgconn.PgError{Code: "57P03"}, ErrUnavailable},
		{"serialization failure", &pgconn.PgError{Code: "40001"}, ErrUnavailable},
		{"deadlock", &pgconn.PgError{Code: "40P01"}, ErrUnavailable},
		{"timeout", fmt.Errorf("write: %w", context.DeadlineExceeded), ErrUnavailable},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := classify(c.err); !errors.Is(got, c.want) {
				t.Fatalf("classify(%v) = %v, want %v", c.err, got, c.want)
			}
		})
	}
}

func TestClassifyOtherErrors(t *testing.T) {
	if classify(nil) != nil {
		t.Fatal("nil")
	}
	err := classify(&pgconn.PgError{Code: "42P01", Message: "relation does not exist"})
	for _, sentinel := range []error{ErrDuplicate, ErrRejected, ErrUnavailable} {
		if errors.Is(err, sentinel) {
			t.Fatalf("undefined table classified as %v", sentinel)
		}
	}
}

func TestRejectedMessageNamesTheProblem(t *testing.T) {
	err := classify(&pgconn.PgError{Code: "23514", Message: "violates check constraint", ConstraintName: "invoices_currency_check"})
	if want := "constraint invoices_currency_check"; !strings.Contains(err.Error(), want) {
		t.Fatalf("%q does not mention %q", err, want)
	}
}

func TestRetryable(t *testing.T) {
	for code, want := range map[string]bool{"40001": true, "40P01": true, "23505": false, "08006": false} {
		if got := isRetryable(&pgconn.PgError{Code: code}); got != want {
			t.Errorf("isRetryable(%s) = %v", code, got)
		}
	}
}
