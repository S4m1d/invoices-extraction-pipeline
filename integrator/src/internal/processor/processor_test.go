package processor

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/correlation"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/db"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/storage"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/testdata"
)

const (
	doc  = "0f8fad5b-d9cb-469f-a165-70867728950e"
	corr = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
)

func message(changes map[string]any) []byte {
	m := map[string]any{"schema_version": 1, "stage": "fields-extractor", "document_id": doc, "correlation_id": corr,
		"object_key": "documents/" + doc + "/extracted.json", "created_at": "2026-10-16T12:00:00Z"}
	for k, v := range changes {
		m[k] = v
	}
	b, _ := json.Marshal(m)
	return b
}

type fakeStorage struct {
	obj      storage.Object
	errs     []error
	calls    int
	corrSeen string
}

func (f *fakeStorage) Get(ctx context.Context, key string) (storage.Object, error) {
	f.calls++
	f.corrSeen = correlation.ID(ctx)
	if len(f.errs) > 0 {
		err := f.errs[0]
		f.errs = f.errs[1:]
		return storage.Object{}, err
	}
	return f.obj, nil
}

type fakeStore struct {
	err     error
	records []db.Record
}

func (f *fakeStore) Write(ctx context.Context, r db.Record) error {
	f.records = append(f.records, r)
	return f.err
}

type fakePublisher struct {
	err       error
	published []map[string]any
}

func (f *fakePublisher) Publish(ctx context.Context, queue string, body []byte, correlationID string) error {
	if f.err != nil {
		return f.err
	}
	var m map[string]any
	json.Unmarshal(body, &m)
	m["_queue"], m["_corr"] = queue, correlationID
	f.published = append(f.published, m)
	return nil
}

type env struct {
	p      *Processor
	st     *fakeStorage
	store  *fakeStore
	pub    *fakePublisher
	sleeps []time.Duration
	logs   *bytes.Buffer
}

func newEnv() *env {
	e := &env{
		st:    &fakeStorage{obj: storage.Object{Data: testdata.Example(), Metadata: map[string]string{"model": "claude-haiku-5-5", "prompt-version": "v3"}}},
		store: &fakeStore{}, pub: &fakePublisher{}, logs: &bytes.Buffer{},
	}
	e.p = New(e.st, e.store, e.pub, slog.New(slog.NewJSONHandler(e.logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	e.p.Sleep = func(d time.Duration) { e.sleeps = append(e.sleeps, d) }
	e.p.Now = func() time.Time { return time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC) }
	return e
}

func (e *env) entries(t *testing.T) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(e.logs.String()), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("log line not JSON: %s", line)
		}
		out = append(out, m)
	}
	return out
}

func (e *env) onlyFailure(t *testing.T) map[string]any {
	t.Helper()
	if len(e.pub.published) != 1 {
		t.Fatalf("published %v, want one failure", e.pub.published)
	}
	f := e.pub.published[0]
	if f["_queue"] != "documents.failed" || f["_corr"] != corr || f["stage"] != "integrator" || f["document_id"] != doc {
		t.Fatalf("failure %v", f)
	}
	return f
}

func handle(t *testing.T, e *env, body []byte) Outcome {
	t.Helper()
	out, err := e.p.Handle(context.Background(), body)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestHappyPath(t *testing.T) {
	e := newEnv()
	if handle(t, e, message(nil)) != Ack {
		t.Fatal("not acked")
	}
	if len(e.store.records) != 1 || len(e.pub.published) != 0 {
		t.Fatalf("records %d, published %v", len(e.store.records), e.pub.published)
	}
	r := e.store.records[0]
	if r.DocumentID != doc || r.CorrelationID != corr || *r.ExtractionModel != "claude-haiku-5-5" || *r.PromptVersion != "v3" ||
		r.Invoice.InvoiceNumber != "INV-2025-000029" || len(r.Invoice.Lines) != 3 {
		t.Fatalf("record %+v", r)
	}
	if e.st.corrSeen != corr {
		t.Fatalf("storage call without correlation id: %q", e.st.corrSeen)
	}
}

func TestMissingMetadataIsNull(t *testing.T) {
	e := newEnv()
	e.st.obj.Metadata = map[string]string{}
	handle(t, e, message(nil))
	if r := e.store.records[0]; r.ExtractionModel != nil || r.PromptVersion != nil {
		t.Fatalf("want nil metadata, got %v %v", r.ExtractionModel, r.PromptVersion)
	}
}

func TestFailures(t *testing.T) {
	cases := []struct {
		name  string
		setup func(e *env)
		code  string
	}{
		{"object not found", func(e *env) { e.st.errs = []error{fmt.Errorf("%w: x", storage.ErrNotFound)} }, "object_not_found"},
		{"storage unavailable", func(e *env) {
			u := fmt.Errorf("%w: x", storage.ErrUnavailable)
			e.st.errs = []error{u, u, u}
		}, "storage_unavailable"},
		{"invalid extraction", func(e *env) { e.st.obj.Data = []byte(`{"supplier_name": "x"}`) }, "invalid_extraction"},
		{"duplicate", func(e *env) { e.store.err = fmt.Errorf("%w: exists", db.ErrDuplicate) }, "duplicate_document"},
		{"rejected", func(e *env) { e.store.err = fmt.Errorf("%w: bad numeric", db.ErrRejected) }, "database_rejected"},
		{"unexpected", func(e *env) { e.store.err = errors.New("boom") }, "internal_error"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newEnv()
			c.setup(e)
			if handle(t, e, message(nil)) != Ack {
				t.Fatal("not acked")
			}
			if f := e.onlyFailure(t); f["error_code"] != c.code {
				t.Fatalf("error_code %v, want %s", f["error_code"], c.code)
			}
		})
	}
}

func TestStorageRetries(t *testing.T) {
	e := newEnv()
	u := fmt.Errorf("%w: x", storage.ErrUnavailable)
	e.st.errs = []error{u, u}
	handle(t, e, message(nil))
	if e.st.calls != 3 || len(e.store.records) != 1 {
		t.Fatalf("calls %d, records %d", e.st.calls, len(e.store.records))
	}
	if fmt.Sprint(e.sleeps) != "[1s 5s]" {
		t.Fatalf("sleeps %v", e.sleeps)
	}
}

func TestNotFoundIsNotRetried(t *testing.T) {
	e := newEnv()
	e.st.errs = []error{fmt.Errorf("%w: x", storage.ErrNotFound)}
	handle(t, e, message(nil))
	if e.st.calls != 1 || len(e.sleeps) != 0 {
		t.Fatalf("calls %d, sleeps %v", e.st.calls, e.sleeps)
	}
}

func TestDatabaseUnavailablePauses(t *testing.T) {
	e := newEnv()
	e.store.err = fmt.Errorf("%w: connection refused", db.ErrUnavailable)
	if handle(t, e, message(nil)) != Pause {
		t.Fatal("want Pause")
	}
	if len(e.pub.published) != 0 {
		t.Fatalf("published %v", e.pub.published)
	}
}

func TestPublisherErrorPropagates(t *testing.T) {
	e := newEnv()
	e.store.err = fmt.Errorf("%w: exists", db.ErrDuplicate)
	e.pub.err = errors.New("broker gone")
	if _, err := e.p.Handle(context.Background(), message(nil)); err == nil {
		t.Fatal("want error (message must not be acked)")
	}
}

func TestInvalidEnvelopeRejected(t *testing.T) {
	e := newEnv()
	if handle(t, e, message(map[string]any{"stage": "ingestor"})) != Reject {
		t.Fatal("want Reject")
	}
	if e.st.calls != 0 || len(e.store.records) != 0 || len(e.pub.published) != 0 {
		t.Fatal("invalid message must not be processed")
	}
}

func TestOneSummaryPerMessage(t *testing.T) {
	for name, setup := range map[string]func(e *env){
		"ok":     func(*env) {},
		"failed": func(e *env) { e.store.err = fmt.Errorf("%w: exists", db.ErrDuplicate) },
	} {
		t.Run(name, func(t *testing.T) {
			e := newEnv()
			setup(e)
			handle(t, e, message(nil))
			var summaries []map[string]any
			for _, m := range e.entries(t) {
				if m["correlation_id"] != corr || m["document_id"] != doc {
					t.Errorf("entry without ids: %v", m)
				}
				if m["msg"] == "message processed" {
					summaries = append(summaries, m)
				}
			}
			if len(summaries) != 1 || summaries[0]["duration_ms"] == nil || summaries[0]["result"] != name {
				t.Fatalf("summaries %v", summaries)
			}
		})
	}
}
