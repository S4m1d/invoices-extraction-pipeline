package api

import (
	"bufio"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

func bufioReader(r io.Reader) *bufio.Reader { return bufio.NewReader(r) }

func TestRouting(t *testing.T) {
	t.Run("unknown path is 404", func(t *testing.T) {
		e := newEnv()
		expectError(t, e.do(httptest.NewRequest(http.MethodGet, "/nope", nil)), http.StatusNotFound, "not_found")
	})
	for path, allow := range map[string]string{"/documents": "POST", "/healthz": "GET"} {
		t.Run("wrong method on "+path+" is 405", func(t *testing.T) {
			e := newEnv()
			rec := e.do(httptest.NewRequest(http.MethodDelete, path, nil))
			expectError(t, rec, http.StatusMethodNotAllowed, "method_not_allowed")
			if got := rec.Header().Get("Allow"); got != allow {
				t.Errorf("Allow %q, want %q", got, allow)
			}
		})
	}
}

type healthResponse struct {
	Status  string   `json:"status"`
	Failing []string `json:"failing"`
}

func TestHealthz(t *testing.T) {
	tests := []struct {
		name        string
		rmq, minio  error
		wantStatus  int
		wantFailing []string
	}{
		{"all up", nil, nil, 200, nil},
		{"rabbitmq down", errDown, nil, 503, []string{"rabbitmq"}},
		{"minio down", nil, errDown, 503, []string{"minio"}},
		{"both down", errDown, errDown, 503, []string{"rabbitmq", "minio"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newEnv()
			e.pub.checkErr, e.storage.checkErr = tt.rmq, tt.minio
			rec := e.do(httptest.NewRequest(http.MethodGet, "/healthz", nil))
			if rec.Code != tt.wantStatus {
				t.Fatalf("status %d, want %d", rec.Code, tt.wantStatus)
			}
			var h healthResponse
			json.Unmarshal(rec.Body.Bytes(), &h)
			wantStatus := "ok"
			if tt.wantStatus != 200 {
				wantStatus = "unavailable"
			}
			if h.Status != wantStatus || len(h.Failing) != len(tt.wantFailing) {
				t.Fatalf("body %s, want status %q failing %v", rec.Body, wantStatus, tt.wantFailing)
			}
			for i := range h.Failing {
				if h.Failing[i] != tt.wantFailing[i] {
					t.Fatalf("failing %v, want %v", h.Failing, tt.wantFailing)
				}
			}
		})
	}

	t.Run("hanging check times out", func(t *testing.T) {
		e := newEnv()
		e.server.healthTimeout = 100 * time.Millisecond
		e.storage.block = true
		start := time.Now()
		rec := e.do(httptest.NewRequest(http.MethodGet, "/healthz", nil))
		if rec.Code != http.StatusServiceUnavailable {
			t.Fatalf("status %d, want 503", rec.Code)
		}
		if d := time.Since(start); d > time.Second {
			t.Fatalf("took %v, want about the health timeout", d)
		}
	})

	t.Run("successful check is not logged", func(t *testing.T) {
		e := newEnv()
		e.do(httptest.NewRequest(http.MethodGet, "/healthz", nil))
		if entries := e.logs.entries(t); len(entries) != 0 {
			t.Fatalf("logged %v, want nothing", entries)
		}
	})

	t.Run("failed check is logged with correlation_id", func(t *testing.T) {
		e := newEnv()
		e.storage.checkErr = errDown
		e.do(httptest.NewRequest(http.MethodGet, "/healthz", nil))
		m := hasLog(e.logs.entries(t), "health check failed")
		if m == nil || m["dependency"] != "minio" || m["correlation_id"] == nil {
			t.Fatalf("missing health failure log, got %v", e.logs.entries(t))
		}
	})
}

func TestLogging(t *testing.T) {
	checkAll := func(t *testing.T, e *env, correlationID string) []map[string]any {
		t.Helper()
		entries := e.logs.entries(t)
		if len(entries) == 0 {
			t.Fatal("nothing logged")
		}
		for _, m := range entries {
			if m["correlation_id"] != correlationID {
				t.Errorf("entry %v: correlation_id %v, want %s", m["msg"], m["correlation_id"], correlationID)
			}
		}
		return entries
	}

	t.Run("accepted upload", func(t *testing.T) {
		e := newEnv()
		id := correlation.New()
		rec := e.upload(fileField(validPDF), field("correlation_id", id))
		var b acceptedBody
		json.Unmarshal(rec.Body.Bytes(), &b)
		entries := checkAll(t, e, id)
		access := hasLog(entries, "request")
		if access == nil || access["document_id"] != b.DocumentID || access["status"] != float64(202) {
			t.Fatalf("access entry %v, want document_id %s and status 202", access, b.DocumentID)
		}
	})

	t.Run("rejected upload uses the ID from the response", func(t *testing.T) {
		e := newEnv()
		resp := expectError(t, e.upload(fileField("hello")), 415, "not_pdf")
		entries := checkAll(t, e, resp.CorrelationID)
		if hasLog(entries, "request rejected") == nil || hasLog(entries, "request") == nil {
			t.Fatalf("missing rejection or access entry: %v", entries)
		}
	})

	t.Run("dependency failure is logged as error", func(t *testing.T) {
		e := newEnv()
		e.pub.publishErr = errDown
		id := correlation.New()
		e.upload(fileField(validPDF), field("correlation_id", id))
		m := hasLog(checkAll(t, e, id), "request failed")
		if m == nil || m["level"] != "ERROR" || m["document_id"] == nil {
			t.Fatalf("error entry %v, want level ERROR with document_id", m)
		}
	})

	for name, setup := range map[string]func(*env){
		"storage": func(e *env) { e.storage.putErr = errDown },
		"queue":   func(e *env) { e.pub.publishErr = errDown },
	} {
		t.Run(name+" failure details are logged, not returned", func(t *testing.T) {
			e := newEnv()
			setup(e)
			resp := expectError(t, e.upload(fileField(validPDF)), 503, "service_unavailable")
			if resp.Error.Message != "service temporarily unavailable, retry later" {
				t.Errorf("response message %q, want the generic one", resp.Error.Message)
			}
			m := hasLog(e.logs.entries(t), "request failed")
			if m == nil || !strings.Contains(m["error"].(string), errDown.Error()) {
				t.Fatalf("log entry %v, want the cause %q", m, errDown)
			}
		})
	}
}

func TestSubstitutionWarnings(t *testing.T) {
	const beforeRead = "request rejected before correlation_id was read, using generated correlation_id"
	const invalid = "client correlation_id invalid, using generated correlation_id"

	t.Run("rejected before correlation_id part", func(t *testing.T) {
		e := newEnv()
		resp := expectError(t, e.upload(fileField("hello"), field("correlation_id", correlation.New())), 415, "not_pdf")
		m := hasLog(e.logs.entries(t), beforeRead)
		if m == nil || m["level"] != "WARN" || m["correlation_id"] != resp.CorrelationID {
			t.Fatalf("missing warning, logs %v", e.logs.entries(t))
		}
	})

	t.Run("rejected before body is read (Content-Length)", func(t *testing.T) {
		e := newEnv()
		body, ct := multipartBody(fileField(validPDF))
		req := httptest.NewRequest(http.MethodPost, "/documents", bytesReader(body))
		req.Header.Set("Content-Type", ct)
		req.ContentLength = MaxRequestBytes + 1
		e.do(req)
		if hasLog(e.logs.entries(t), beforeRead) == nil {
			t.Fatalf("missing warning, logs %v", e.logs.entries(t))
		}
	})

	t.Run("invalid client value", func(t *testing.T) {
		e := newEnv()
		resp := expectError(t, e.upload(fileField(validPDF), field("correlation_id", "run-42")), 400, "invalid_correlation_id")
		m := hasLog(e.logs.entries(t), invalid)
		if m == nil || m["client_correlation_id"] != "run-42" || m["correlation_id"] != resp.CorrelationID {
			t.Fatalf("warning %v, want client value and generated ID", m)
		}
		if hasLog(e.logs.entries(t), beforeRead) != nil {
			t.Errorf("also logged %q", beforeRead)
		}
	})

	t.Run("invalid client value is truncated", func(t *testing.T) {
		e := newEnv()
		value := strings.Repeat("abcdefghij", 20)
		e.upload(fileField(validPDF), field("correlation_id", value))
		m := hasLog(e.logs.entries(t), invalid)
		if m == nil || len(m["client_correlation_id"].(string)) != 64 {
			t.Fatalf("warning %v, want value truncated to 64", m)
		}
	})

	t.Run("no warning when the client sent no correlation_id", func(t *testing.T) {
		e := newEnv()
		expectError(t, e.upload(field("foo", "bar")), 400, "unknown_field") // rejected mid-body: warns
		if hasLog(e.logs.entries(t), beforeRead) == nil {
			t.Fatalf("rejection mid-body should warn")
		}

		e = newEnv()
		expectError(t, e.upload(), 400, "invalid_file_part") // whole body read, no correlation_id sent
		expectError(t, e.upload(field("correlation_id", "")), 400, "invalid_file_part")
		for _, m := range e.logs.entries(t) {
			if m["level"] == "WARN" {
				t.Fatalf("unexpected warning %v", m)
			}
		}
	})

	t.Run("no warning on success", func(t *testing.T) {
		e := newEnv()
		e.upload(fileField(validPDF))
		for _, m := range e.logs.entries(t) {
			if m["level"] == "WARN" {
				t.Fatalf("unexpected warning %v", m)
			}
		}
	})
}

func TestConcurrencyLimit(t *testing.T) {
	e := newEnvWithLimit(1)
	e.storage.putGate = make(chan struct{})
	e.storage.entered = make(chan struct{})

	first := make(chan int)
	go func() { first <- e.upload(fileField(validPDF)).Code }()
	<-e.storage.entered // the first upload holds the only slot

	resp := expectError(t, e.upload(fileField(validPDF)), http.StatusServiceUnavailable, "server_busy")
	entries := e.logs.entries(t)
	warn := hasLog(entries, "request rejected before correlation_id was read, using generated correlation_id")
	if warn == nil || warn["correlation_id"] != resp.CorrelationID {
		t.Errorf("missing substitution warning, logs %v", entries)
	}
	if m := hasLog(entries, "request rejected"); m == nil || m["level"] != "INFO" {
		t.Errorf("busy rejection should be logged at INFO, got %v", m)
	}

	t.Run("healthz is not limited", func(t *testing.T) {
		if rec := e.do(httptest.NewRequest(http.MethodGet, "/healthz", nil)); rec.Code != http.StatusOK {
			t.Fatalf("status %d, want 200", rec.Code)
		}
	})

	close(e.storage.putGate)
	if code := <-first; code != http.StatusAccepted {
		t.Fatalf("first upload: status %d, want 202", code)
	}
	e.storage.putGate = nil
	t.Run("slot is released after the upload finishes", func(t *testing.T) {
		if rec := e.upload(fileField(validPDF)); rec.Code != http.StatusAccepted {
			t.Fatalf("status %d, want 202; body %s", rec.Code, rec.Body)
		}
	})
}

func TestSlotReleasedAfterRejection(t *testing.T) {
	e := newEnvWithLimit(1)
	expectError(t, e.upload(fileField("hello")), 415, "not_pdf")
	e.storage.putErr = errDown
	expectError(t, e.upload(fileField(validPDF)), 503, "service_unavailable")
	e.storage.putErr = nil
	if rec := e.upload(fileField(validPDF)); rec.Code != http.StatusAccepted {
		t.Fatalf("status %d, want 202 (slot leaked?)", rec.Code)
	}
}
