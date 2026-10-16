//go:build integration

package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	amqp "github.com/rabbitmq/amqp091-go"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/db"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/processor"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/storage"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/testdata"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/testutil"
)

type env struct {
	t       *testing.T
	amqpURL string
	mgmt    *testutil.Management
	proxy   *testutil.Proxy
	store   *db.Store
	dbURL   string
	mc      *minio.Client
	w       *Worker
	ch      *amqp.Channel
}

func setup(t *testing.T) *env {
	ReconnectWait = 200 * time.Millisecond
	e := &env{t: t}
	e.amqpURL, e.mgmt = testutil.Vhost(t)
	e.proxy = testutil.NewProxy(t, testutil.PostgresTarget(t))
	e.dbURL = testutil.TempDatabase(t, e.proxy.Addr)
	ctx := context.Background()
	var err error
	if e.store, err = db.Open(ctx, e.dbURL); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(e.store.Close)
	if err := e.store.Migrate(ctx, slog.New(slog.DiscardHandler)); err != nil {
		t.Fatal(err)
	}
	objects, err := storage.NewMinio(testutil.Env(t, "MINIO_ENDPOINT"), testutil.Env(t, "MINIO_ACCESS_KEY"),
		testutil.Env(t, "MINIO_SECRET_KEY"), false)
	if err != nil {
		t.Fatal(err)
	}
	e.mc, _ = minio.New(os.Getenv("MINIO_ENDPOINT"), &minio.Options{
		Creds: credentials.NewStaticV4(os.Getenv("MINIO_ACCESS_KEY"), os.Getenv("MINIO_SECRET_KEY"), "")})
	log := slog.New(slog.DiscardHandler)
	e.w = &Worker{URL: e.amqpURL, DBCheck: e.store.Ping, CheckInterval: 200 * time.Millisecond, Log: log}
	e.w.Handler = processor.New(objects, e.store, e.w, log)

	e.openChannel()
	return e
}

// openChannel opens the test's own AMQP connection (again after CloseConnections closed it too).
func (e *env) openChannel() {
	conn, err := amqp.Dial(e.amqpURL)
	if err != nil {
		e.t.Fatal(err)
	}
	e.t.Cleanup(func() { conn.Close() })
	e.ch, _ = conn.Channel()
}

func (e *env) start() {
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	wg.Add(1)
	go func() { defer wg.Done(); e.w.Run(ctx) }()
	e.t.Cleanup(func() { cancel(); wg.Wait() })
	if !waitUntil(func() bool { return e.w.Connected.Load() }, 10*time.Second) {
		e.t.Fatal("worker did not connect")
	}
}

func (e *env) submit(data []byte) string {
	e.t.Helper()
	doc := uuid.NewString()
	key := "documents/" + doc + "/extracted.json"
	if _, err := e.mc.PutObject(context.Background(), "invoices", key, bytes.NewReader(data), int64(len(data)),
		minio.PutObjectOptions{}); err != nil {
		e.t.Fatal(err)
	}
	body, _ := json.Marshal(map[string]any{"schema_version": 1, "stage": "fields-extractor", "document_id": doc,
		"correlation_id": uuid.NewString(), "object_key": key, "created_at": time.Now().UTC().Format(time.RFC3339Nano)})
	if err := e.ch.Publish("", "documents.fields_extracted", false, false, amqp.Publishing{DeliveryMode: 2, Body: body}); err != nil {
		e.t.Fatal(err)
	}
	return doc
}

func (e *env) count(queue string) int {
	q, err := e.ch.QueueDeclarePassive(queue, true, false, false, false, nil)
	if err != nil {
		e.t.Fatal(err)
	}
	return q.Messages
}

// stored returns document ids in write order.
func (e *env) stored() []string {
	conn := testutil.AdminConn(e.t, e.dbURL)
	rows, err := conn.Query(context.Background(), "SELECT document_id::text FROM invoices ORDER BY created_at")
	if err != nil {
		e.t.Fatal(err)
	}
	var out []string
	for rows.Next() {
		var d string
		rows.Scan(&d)
		out = append(out, d)
	}
	return out
}

func waitUntil(cond func() bool, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if cond() {
			return true
		}
		time.Sleep(50 * time.Millisecond)
	}
	return false
}

func TestProcessesAndAcks(t *testing.T) {
	e := setup(t)
	e.start()
	doc := e.submit(testdata.Example())
	if !waitUntil(func() bool { return len(e.stored()) == 1 }, 10*time.Second) || e.stored()[0] != doc {
		t.Fatalf("stored %v", e.stored())
	}
	time.Sleep(300 * time.Millisecond)
	if e.count("documents.fields_extracted") != 0 || e.count("documents.failed") != 0 {
		t.Fatal("message not acked or failure published")
	}
}

func TestDatabaseOutagePausesAndResumesInOrder(t *testing.T) {
	e := setup(t)
	e.start()
	e.proxy.Down()
	a, b := e.submit(testdata.Example()), e.submit(testdata.Example())
	if !waitUntil(func() bool { return e.w.Paused.Load() }, 10*time.Second) {
		t.Fatal("worker did not pause")
	}
	time.Sleep(time.Second) // several failed checks
	if e.w.Connected.Load() || e.count("documents.fields_extracted") != 2 || e.count("documents.failed") != 0 {
		t.Fatalf("connected=%v queued=%d failed=%d", e.w.Connected.Load(), e.count("documents.fields_extracted"),
			e.count("documents.failed"))
	}
	e.proxy.Up(t, "")
	if !waitUntil(func() bool { return len(e.stored()) == 2 }, 15*time.Second) {
		t.Fatalf("stored %v after recovery", e.stored())
	}
	if got := e.stored(); got[0] != a || got[1] != b {
		t.Fatalf("order %v, want [%s %s]", got, a, b)
	}
	if e.w.Paused.Load() || e.count("documents.failed") != 0 {
		t.Fatal("still paused or failures published")
	}
}

func TestUnroutableFailureIsNotAcked(t *testing.T) {
	e := setup(t)
	e.start()
	if _, err := e.ch.QueueDelete("documents.failed", false, false, false); err != nil {
		t.Fatal(err)
	}
	e.submit([]byte(`{"supplier_name": "x"}`)) // invalid_extraction needs documents.failed
	time.Sleep(time.Second)
	if e.count("documents.fields_extracted") != 1 {
		t.Fatal("message was acked although its failure could not be published")
	}
	e.ch.QueueDeclare("documents.failed", true, false, false, false, nil)
	if !waitUntil(func() bool { return e.count("documents.failed") == 1 }, 15*time.Second) {
		t.Fatal("failure not published after the queue came back")
	}
}

func TestReconnectsAfterConnectionLoss(t *testing.T) {
	e := setup(t)
	e.start()
	if e.mgmt.CloseConnections(t) == 0 {
		t.Fatal("no connections to close")
	}
	time.Sleep(300 * time.Millisecond)
	if !waitUntil(func() bool { return e.w.Connected.Load() }, 10*time.Second) {
		t.Fatal("worker did not reconnect")
	}
	e.openChannel() // the test's own connection was closed too
	doc := e.submit(testdata.Example())
	if !waitUntil(func() bool { return len(e.stored()) == 1 }, 10*time.Second) || e.stored()[0] != doc {
		t.Fatalf("stored %v", e.stored())
	}
}

func TestInvalidMessageIsDeadLettered(t *testing.T) {
	e := setup(t)
	e.start()
	e.ch.Publish("", "documents.fields_extracted", false, false, amqp.Publishing{Body: []byte(`{"not": "an envelope"}`)})
	if !waitUntil(func() bool { return e.count("documents.fields_extracted.dlq") == 1 }, 10*time.Second) {
		t.Fatal("not dead-lettered")
	}
}
