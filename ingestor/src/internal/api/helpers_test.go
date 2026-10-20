package api

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/textproto"
	"strings"
	"sync"
	"testing"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

const validPDF = "%PDF-1.4\n%%EOF\n"

// fakeStorage and fakePublisher record calls in one shared, ordered log.
type calls struct {
	mu  sync.Mutex
	log []string
}

func (c *calls) add(s string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.log = append(c.log, s)
}

func (c *calls) list() []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]string(nil), c.log...)
}

type fakeStorage struct {
	calls    *calls
	putGate  chan struct{} // if set, PutPDF signals entered and waits for release
	entered  chan struct{}
	putErr   error
	checkErr error
	block    bool // Check waits for ctx to end

	key           string
	data          []byte
	correlationID string
}

func (f *fakeStorage) PutPDF(ctx context.Context, key string, data []byte) error {
	f.calls.add("put")
	if f.putGate != nil {
		f.entered <- struct{}{}
		<-f.putGate
	}
	f.key, f.data, f.correlationID = key, data, correlation.ID(ctx)
	return f.putErr
}

func (f *fakeStorage) Check(ctx context.Context) error {
	if f.block {
		<-ctx.Done()
		return ctx.Err()
	}
	return f.checkErr
}

type fakePublisher struct {
	calls      *calls
	publishErr error
	checkErr   error
	block      bool

	body          []byte
	correlationID string
}

func (f *fakePublisher) Publish(ctx context.Context, body []byte) error {
	f.calls.add("publish")
	f.body, f.correlationID = body, correlation.ID(ctx)
	return f.publishErr
}

func (f *fakePublisher) Check(ctx context.Context) error {
	if f.block {
		<-ctx.Done()
		return ctx.Err()
	}
	return f.checkErr
}

// logBuffer is a concurrency-safe log sink.
type logBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *logBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *logBuffer) entries(t *testing.T) []map[string]any {
	t.Helper()
	b.mu.Lock()
	defer b.mu.Unlock()
	var out []map[string]any
	sc := bufio.NewScanner(bytes.NewReader(b.buf.Bytes()))
	for sc.Scan() {
		var m map[string]any
		if err := json.Unmarshal(sc.Bytes(), &m); err != nil {
			t.Fatalf("log line is not JSON: %s", sc.Text())
		}
		out = append(out, m)
	}
	return out
}

type env struct {
	server  *Server
	handler http.Handler
	storage *fakeStorage
	pub     *fakePublisher
	calls   *calls
	logs    *logBuffer
}

func newEnv() *env { return newEnvWithLimit(20) }

func newEnvWithLimit(maxUploads int) *env {
	c := &calls{}
	logs := &logBuffer{}
	st := &fakeStorage{calls: c}
	pub := &fakePublisher{calls: c}
	s := NewServer(st, pub, slog.New(slog.NewJSONHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})), maxUploads)
	return &env{server: s, handler: s.Handler(), storage: st, pub: pub, calls: c, logs: logs}
}

// part is one multipart part. A non-empty filename makes it a file part.
type part struct {
	name, filename, content string
}

func fileField(content string) part { return part{"file", "invoice.pdf", content} }
func field(name, value string) part { return part{name, "", value} }

func multipartBody(parts ...part) (body []byte, contentType string) {
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	for _, p := range parts {
		h := textproto.MIMEHeader{}
		disp := fmt.Sprintf(`form-data; name=%q`, p.name)
		if p.filename != "" {
			disp += fmt.Sprintf(`; filename=%q`, p.filename)
			h.Set("Content-Type", "application/pdf")
		}
		h.Set("Content-Disposition", disp)
		w, _ := mw.CreatePart(h)
		io.WriteString(w, p.content)
	}
	mw.Close()
	return buf.Bytes(), mw.FormDataContentType()
}

func (e *env) do(req *http.Request) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	e.handler.ServeHTTP(rec, req)
	return rec
}

func (e *env) upload(parts ...part) *httptest.ResponseRecorder {
	body, ct := multipartBody(parts...)
	req := httptest.NewRequest(http.MethodPost, "/documents", bytes.NewReader(body))
	req.Header.Set("Content-Type", ct)
	return e.do(req)
}

type errorResponse struct {
	Error struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
	CorrelationID string `json:"correlation_id"`
}

// expectError checks status, JSON error body, code and a generated or given correlation ID.
func expectError(t *testing.T, rec *httptest.ResponseRecorder, status int, code string) errorResponse {
	t.Helper()
	if rec.Code != status {
		t.Fatalf("status %d, want %d; body %s", rec.Code, status, rec.Body)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type %q, want application/json", ct)
	}
	var e errorResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &e); err != nil {
		t.Fatalf("body is not JSON: %s", rec.Body)
	}
	if e.Error.Code != code {
		t.Fatalf("error code %q, want %q (message %q)", e.Error.Code, code, e.Error.Message)
	}
	if e.Error.Message == "" {
		t.Errorf("empty error message")
	}
	if _, ok := correlation.Parse(e.CorrelationID); !ok {
		t.Errorf("correlation_id %q is not a UUID", e.CorrelationID)
	}
	return e
}

func expectNoSideEffects(t *testing.T, e *env) {
	t.Helper()
	if got := e.calls.list(); len(got) != 0 {
		t.Errorf("unexpected calls to storage/queue: %v", got)
	}
}

func hasLog(entries []map[string]any, msg string) map[string]any {
	for _, m := range entries {
		if m["msg"] == msg {
			return m
		}
	}
	return nil
}

var errDown = errors.New("connection refused")

func contains(s, sub string) bool { return strings.Contains(s, sub) }

func bytesReader(b []byte) io.Reader { return bytes.NewReader(b) }
