// Package db writes invoices to PostgreSQL (integrator/specs/data-model.md) and applies migrations.
package db

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"
	"github.com/pressly/goose/v3/lock"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/extraction"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/migrations"
)

var (
	// ErrDuplicate: the document_id is already stored (error code duplicate_document).
	ErrDuplicate = errors.New("document already stored")
	// ErrRejected: PostgreSQL rejected the data (error code database_rejected).
	ErrRejected = errors.New("database rejected the data")
	// ErrUnavailable: PostgreSQL is unreachable; the worker pauses (overview, Database unavailable).
	ErrUnavailable = errors.New("database unavailable")
)

const writeTimeout = 30 * time.Second

// Record is one document to write.
type Record struct {
	DocumentID      string
	CorrelationID   string
	ExtractionModel *string
	PromptVersion   *string
	Invoice         extraction.Invoice
}

type Store struct {
	pool *pgxpool.Pool
	now  func() time.Time
}

func Open(ctx context.Context, url string) (*Store, error) {
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		return nil, fmt.Errorf("database url: %w", err)
	}
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("database pool: %w", err)
	}
	return &Store{pool: pool, now: time.Now}, nil
}

func (s *Store) Close() { s.pool.Close() }

// Migrate applies the embedded migrations under a PostgreSQL advisory lock (spec: Schema migrations).
func (s *Store) Migrate(ctx context.Context, log *slog.Logger) error {
	locker, err := lock.NewPostgresSessionLocker()
	if err != nil {
		return err
	}
	sqlDB := stdlib.OpenDBFromPool(s.pool)
	defer sqlDB.Close()
	provider, err := goose.NewProvider(goose.DialectPostgres, sqlDB, migrations.FS, goose.WithSessionLocker(locker))
	if err != nil {
		return fmt.Errorf("migrations: %w", err)
	}
	results, err := provider.Up(ctx)
	if err != nil {
		return fmt.Errorf("migrations: %w", err)
	}
	for _, r := range results {
		log.Info("migration applied", "version", r.Source.Version, "file", r.Source.Path, "duration_ms", r.Duration.Milliseconds())
	}
	var tester bool
	if err := s.pool.QueryRow(ctx, "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tester')").Scan(&tester); err != nil {
		return fmt.Errorf("check role tester: %w", err)
	}
	if !tester {
		log.Warn("role tester does not exist: read access for the tester was not granted (created by infra/)")
	}
	return nil
}

// Ping checks that the database answers (health endpoint, pause checks).
func (s *Store) Ping(ctx context.Context) error {
	var one int
	return s.pool.QueryRow(ctx, "SELECT 1").Scan(&one)
}

// Write stores the invoice and its lines in one transaction. Errors wrap ErrDuplicate,
// ErrRejected or ErrUnavailable; serialization failures and deadlocks are retried once.
func (s *Store) Write(ctx context.Context, r Record) error {
	err := s.write(ctx, r)
	if isRetryable(err) {
		err = s.write(ctx, r)
	}
	return classify(err)
}

func (s *Store) write(ctx context.Context, r Record) error {
	ctx, cancel := context.WithTimeout(ctx, writeTimeout)
	defer cancel()
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		inv := r.Invoice
		id := uuid.NewString()
		now := s.now().UTC()
		// The insert comes first: the unique constraint on document_id is the duplicate check.
		_, err := tx.Exec(ctx, `
			INSERT INTO invoices (id, document_id, correlation_id, supplier_name, recipient_name, invoice_number,
				issue_date, due_date, currency, subtotal, tax_total, grand_total, tax_rate, extraction_model,
				prompt_version, created_at, updated_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7::date, $8::date, $9, $10::numeric, $11::numeric, $12::numeric,
				$13::numeric, $14, $15, $16, $16)`,
			id, r.DocumentID, r.CorrelationID, inv.SupplierName, inv.RecipientName, inv.InvoiceNumber,
			inv.IssueDate, inv.DueDate, inv.Currency, inv.Subtotal, inv.TaxTotal, inv.GrandTotal, inv.TaxRate,
			r.ExtractionModel, r.PromptVersion, now)
		if err != nil {
			return err
		}
		lineNos := make([]int32, len(inv.Lines))
		descs := make([]string, len(inv.Lines))
		qtys := make([]*string, len(inv.Lines))
		prices := make([]*string, len(inv.Lines))
		amounts := make([]string, len(inv.Lines))
		for i, l := range inv.Lines {
			lineNos[i], descs[i], qtys[i], prices[i], amounts[i] = int32(l.LineNo), l.Description, l.Quantity, l.UnitPrice, l.Amount
		}
		_, err = tx.Exec(ctx, `
			INSERT INTO invoice_lines (invoice_id, line_no, description, quantity, unit_price, amount)
			SELECT $1, t.line_no, t.description, t.quantity::numeric, t.unit_price::numeric, t.amount::numeric
			FROM unnest($2::int[], $3::text[], $4::text[], $5::text[], $6::text[])
				AS t(line_no, description, quantity, unit_price, amount)`,
			id, lineNos, descs, qtys, prices, amounts)
		return err
	})
}

func isRetryable(err error) bool {
	var pg *pgconn.PgError
	return errors.As(err, &pg) && (pg.Code == "40001" || pg.Code == "40P01")
}

func classify(err error) error {
	if err == nil {
		return nil
	}
	var pg *pgconn.PgError
	if errors.As(err, &pg) {
		switch {
		case pg.Code == "23505" && pg.ConstraintName == "invoices_document_id_key":
			return fmt.Errorf("%w: %s", ErrDuplicate, pg.Detail)
		case pg.Code[:2] == "22" || pg.Code[:2] == "23":
			return fmt.Errorf("%w: %s (%s)", ErrRejected, pg.Message, describe(pg))
		case pg.Code[:2] == "08" || pg.Code[:2] == "53" || pg.Code[:2] == "57" || isRetryable(err):
			return fmt.Errorf("%w: %s (%s)", ErrUnavailable, pg.Message, pg.Code)
		}
		return fmt.Errorf("database error %s: %s", pg.Code, pg.Message)
	}
	var netErr net.Error
	var connectErr *pgconn.ConnectError
	if errors.As(err, &connectErr) || errors.As(err, &netErr) || errors.Is(err, context.DeadlineExceeded) ||
		pgconn.SafeToRetry(err) || pgconn.Timeout(err) {
		return fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	return err
}

func describe(pg *pgconn.PgError) string {
	parts := pg.Code
	if pg.ConstraintName != "" {
		parts += ", constraint " + pg.ConstraintName
	}
	if pg.ColumnName != "" {
		parts += ", column " + pg.ColumnName
	}
	if pg.Where != "" {
		parts += ", " + pg.Where
	}
	return parts
}
