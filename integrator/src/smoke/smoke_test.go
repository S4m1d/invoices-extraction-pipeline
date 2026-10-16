//go:build smoke

// Package smoke checks a running integrator against real RabbitMQ, MinIO and PostgreSQL.
// Queues and the bucket must exist (infra).
//
//	AMQP_URL=... MINIO_ENDPOINT=... MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... DATABASE_URL=... \
//	go test -tags smoke -count=1 -v ./smoke/
package smoke

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	amqp "github.com/rabbitmq/amqp091-go"
)

func env(t *testing.T, name string) string {
	t.Helper()
	v := os.Getenv(name)
	if v == "" {
		t.Fatalf("%s is not set", name)
	}
	return v
}

// example is the extraction-schema.md example (matches the golden set for inv_00000029).
func example(t *testing.T) []byte {
	t.Helper()
	spec, err := os.ReadFile("../../../fields-extractor/specs/extraction-schema.md")
	if err != nil {
		t.Fatal(err)
	}
	s := strings.SplitN(string(spec), "## Example\n\n```json\n", 2)[1]
	return []byte(strings.SplitN(s, "\n```", 2)[0])
}

type harness struct {
	t     *testing.T
	ch    *amqp.Channel
	mc    *minio.Client
	db    *pgx.Conn
	model string
}

func setup(t *testing.T) *harness {
	conn, err := amqp.Dial(env(t, "AMQP_URL"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	ch, err := conn.Channel()
	if err != nil {
		t.Fatal(err)
	}
	mc, err := minio.New(env(t, "MINIO_ENDPOINT"), &minio.Options{
		Creds: credentials.NewStaticV4(env(t, "MINIO_ACCESS_KEY"), env(t, "MINIO_SECRET_KEY"), ""),
	})
	if err != nil {
		t.Fatal(err)
	}
	db, err := pgx.Connect(context.Background(), env(t, "DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close(context.Background()) })
	return &harness{t: t, ch: ch, mc: mc, db: db}
}

// submit stores extracted.json for a new (or given) document and publishes the envelope.
func (h *harness) submit(doc string, data []byte) (string, string) {
	h.t.Helper()
	if doc == "" {
		doc = uuid.NewString()
	}
	corr := uuid.NewString()
	key := "documents/" + doc + "/extracted.json"
	_, err := h.mc.PutObject(context.Background(), "invoices", key, bytes.NewReader(data), int64(len(data)),
		minio.PutObjectOptions{ContentType: "application/json",
			UserMetadata: map[string]string{"model": "claude-haiku-5-5", "prompt-version": "v3"}})
	if err != nil {
		h.t.Fatal(err)
	}
	h.publish(map[string]any{"schema_version": 1, "stage": "fields-extractor", "document_id": doc,
		"correlation_id": corr, "object_key": key, "created_at": time.Now().UTC().Format(time.RFC3339Nano)})
	return doc, corr
}

func (h *harness) publish(m any) {
	h.t.Helper()
	body, _ := json.Marshal(m)
	if err := h.ch.Publish("", "documents.fields_extracted", false, false,
		amqp.Publishing{DeliveryMode: amqp.Persistent, ContentType: "application/json", Body: body}); err != nil {
		h.t.Fatal(err)
	}
}

// take gets messages from queue until match is true; others are requeued.
func (h *harness) take(queue string, match func(map[string]any) bool) map[string]any {
	h.t.Helper()
	var requeue []amqp.Delivery
	defer func() {
		for _, d := range requeue {
			d.Nack(false, true)
		}
	}()
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		d, ok, err := h.ch.Get(queue, false)
		if err != nil {
			h.t.Fatal(err)
		}
		if !ok {
			time.Sleep(100 * time.Millisecond)
			continue
		}
		var m map[string]any
		if json.Unmarshal(d.Body, &m) == nil && match(m) {
			d.Ack(false)
			return m
		}
		requeue = append(requeue, d)
	}
	h.t.Fatalf("no matching message on %s", queue)
	return nil
}

func (h *harness) waitRow(doc string) bool {
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		var n int
		h.db.QueryRow(context.Background(), "SELECT count(*) FROM invoices WHERE document_id = $1", doc).Scan(&n)
		if n == 1 {
			return true
		}
		time.Sleep(100 * time.Millisecond)
	}
	return false
}

func TestSmoke(t *testing.T) {
	h := setup(t)
	ctx := context.Background()

	t.Run("extraction is written", func(t *testing.T) {
		doc, corr := h.submit("", example(t))
		if !h.waitRow(doc) {
			t.Fatal("no invoices row")
		}
		var supplier, number, currency, grand, model, prompt, issue, rowCorr string
		err := h.db.QueryRow(ctx, `SELECT supplier_name, invoice_number, currency, grand_total::text,
			extraction_model, prompt_version, issue_date::text, correlation_id::text
			FROM invoices WHERE document_id = $1`, doc).
			Scan(&supplier, &number, &currency, &grand, &model, &prompt, &issue, &rowCorr)
		if err != nil {
			t.Fatal(err)
		}
		want := []string{"Willowmere Halcyon Ltd", "INV-2025-000029", "GBP", "928.86", "claude-haiku-5-5", "v3", "2025-07-25", corr}
		got := []string{supplier, number, currency, grand, model, prompt, issue, rowCorr}
		for i := range want {
			if got[i] != want[i] {
				t.Errorf("column %d: got %q, want %q", i, got[i], want[i])
			}
		}
		rows, _ := h.db.Query(ctx, `SELECT l.line_no, l.description, l.amount::text FROM invoice_lines l
			JOIN invoices i ON i.id = l.invoice_id WHERE i.document_id = $1 ORDER BY l.line_no`, doc)
		var lines []string
		for rows.Next() {
			var n int
			var d, a string
			rows.Scan(&n, &d, &a)
			lines = append(lines, d+" "+a)
		}
		if strings.Join(lines, "|") != "Premium support add-on 268.21|Additional user seats 181.62|Growth plan — monthly subscription 324.22" {
			t.Errorf("lines %v", lines)
		}
	})

	t.Run("duplicate document fails and keeps the row", func(t *testing.T) {
		doc, _ := h.submit("", example(t))
		if !h.waitRow(doc) {
			t.Fatal("no invoices row")
		}
		h.submit(doc, example(t))
		fail := h.take("documents.failed", func(m map[string]any) bool { return m["document_id"] == doc })
		if fail["stage"] != "integrator" || fail["error_code"] != "duplicate_document" {
			t.Fatalf("failure %v", fail)
		}
		var n int
		h.db.QueryRow(ctx, "SELECT count(*) FROM invoices WHERE document_id = $1", doc).Scan(&n)
		if n != 1 {
			t.Fatalf("%d rows, want 1", n)
		}
	})

	t.Run("value rejected by the database", func(t *testing.T) {
		var x map[string]any
		json.Unmarshal(example(t), &x)
		x["grand_total"] = "12,3"
		data, _ := json.Marshal(x)
		doc, _ := h.submit("", data)
		fail := h.take("documents.failed", func(m map[string]any) bool { return m["document_id"] == doc })
		if fail["error_code"] != "database_rejected" {
			t.Fatalf("failure %v", fail)
		}
	})

	t.Run("undecodable extraction", func(t *testing.T) {
		doc, _ := h.submit("", []byte(`{"supplier_name": "x"}`))
		fail := h.take("documents.failed", func(m map[string]any) bool { return m["document_id"] == doc })
		if fail["error_code"] != "invalid_extraction" {
			t.Fatalf("failure %v", fail)
		}
	})

	t.Run("invalid message is dead-lettered", func(t *testing.T) {
		marker := uuid.NewString()
		h.publish(map[string]any{"marker": marker})
		h.take("documents.fields_extracted.dlq", func(m map[string]any) bool { return m["marker"] == marker })
	})
}
