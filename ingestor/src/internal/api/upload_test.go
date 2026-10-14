package api

import (
	"bytes"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

// bodyOfSize builds a valid upload whose whole multipart body is exactly n bytes.
func bodyOfSize(t *testing.T, n int) ([]byte, string) {
	t.Helper()
	empty, _ := multipartBody(fileField(validPDF))
	pad := n - len(empty)
	if pad < 0 {
		t.Fatalf("size %d is below multipart overhead", n)
	}
	body, ct := multipartBody(fileField(validPDF + strings.Repeat("x", pad)))
	if len(body) != n {
		t.Fatalf("built body of %d bytes, want %d", len(body), n)
	}
	return body, ct
}

func TestSizeLimit(t *testing.T) {
	t.Run("exactly 5 MiB is accepted", func(t *testing.T) {
		e := newEnv()
		body, ct := bodyOfSize(t, MaxRequestBytes)
		req := httptest.NewRequest(http.MethodPost, "/documents", bytes.NewReader(body))
		req.Header.Set("Content-Type", ct)
		if rec := e.do(req); rec.Code != http.StatusAccepted {
			t.Fatalf("status %d, want 202; body %s", rec.Code, rec.Body)
		}
	})

	t.Run("one byte over is rejected", func(t *testing.T) {
		e := newEnv()
		body, ct := bodyOfSize(t, MaxRequestBytes+1)
		req := httptest.NewRequest(http.MethodPost, "/documents", bytes.NewReader(body))
		req.Header.Set("Content-Type", ct)
		expectError(t, e.do(req), http.StatusRequestEntityTooLarge, "file_too_large")
		expectNoSideEffects(t, e)
	})

	t.Run("Content-Length over the limit is rejected without reading the body", func(t *testing.T) {
		e := newEnv()
		body, ct := multipartBody(fileField(validPDF))
		r := &countingReader{r: bytes.NewReader(body)}
		req := httptest.NewRequest(http.MethodPost, "/documents", r)
		req.Header.Set("Content-Type", ct)
		req.ContentLength = MaxRequestBytes + 1
		expectError(t, e.do(req), http.StatusRequestEntityTooLarge, "file_too_large")
		if r.n != 0 {
			t.Errorf("read %d body bytes, want 0", r.n)
		}
		expectNoSideEffects(t, e)
	})

	t.Run("chunked body over the limit is rejected", func(t *testing.T) {
		e := newEnv()
		body, ct := bodyOfSize(t, MaxRequestBytes+1024)
		req := httptest.NewRequest(http.MethodPost, "/documents", io.NopCloser(bytes.NewReader(body)))
		req.Header.Set("Content-Type", ct)
		req.ContentLength = -1
		req.TransferEncoding = []string{"chunked"}
		expectError(t, e.do(req), http.StatusRequestEntityTooLarge, "file_too_large")
		expectNoSideEffects(t, e)
	})
}

type countingReader struct {
	r io.Reader
	n int
}

func (c *countingReader) Read(p []byte) (int, error) {
	n, err := c.r.Read(p)
	c.n += n
	return n, err
}

func TestR1ContentType(t *testing.T) {
	body, ct := multipartBody(fileField(validPDF))
	boundary := strings.TrimPrefix(ct, "multipart/form-data; boundary=")
	for name, contentType := range map[string]string{
		"missing":         "",
		"json":            "application/json",
		"pdf":             "application/pdf",
		"multipart/mixed": "multipart/mixed; boundary=" + boundary,
		"no boundary":     "multipart/form-data",
		"unparseable":     "multipart/form-data; boundary",
	} {
		t.Run(name, func(t *testing.T) {
			e := newEnv()
			req := httptest.NewRequest(http.MethodPost, "/documents", bytes.NewReader(body))
			if contentType != "" {
				req.Header.Set("Content-Type", contentType)
			}
			expectError(t, e.do(req), http.StatusUnsupportedMediaType, "invalid_content_type")
			expectNoSideEffects(t, e)
		})
	}
}

func TestRejections(t *testing.T) {
	tests := []struct {
		name   string
		parts  []part
		status int
		code   string
	}{
		{"R2 no file part", []part{field("correlation_id", correlation.New())}, 400, "invalid_file_part"},
		{"R2 no parts at all", nil, 400, "invalid_file_part"},
		{"R2 two file parts", []part{fileField(validPDF), fileField(validPDF)}, 400, "invalid_file_part"},
		{"R2 file under another name", []part{fileField(validPDF), {"attachment", "x.pdf", validPDF}}, 400, "invalid_file_part"},
		{"R3 empty file", []part{fileField("")}, 400, "empty_file"},
		{"R4 not a PDF", []part{fileField("hello")}, 415, "not_pdf"},
		{"R4 PDF signature not at start", []part{fileField(" %PDF-1.4")}, 415, "not_pdf"},
		{"R4 truncated signature", []part{fileField("%PDF")}, 415, "not_pdf"},
		{"R5 not a UUID", []part{fileField(validPDF), field("correlation_id", "run-42")}, 400, "invalid_correlation_id"},
		{"R5 UUID with braces", []part{fileField(validPDF), field("correlation_id", "{"+correlation.New()+"}")}, 400, "invalid_correlation_id"},
		{"R5 two correlation_id fields", []part{field("correlation_id", correlation.New()), field("correlation_id", correlation.New()), fileField(validPDF)}, 400, "invalid_correlation_id"},
		{"R5 correlation_id sent as a file", []part{fileField(validPDF), {"correlation_id", "id.txt", correlation.New()}}, 400, "invalid_correlation_id"},
		{"R6 unknown field", []part{fileField(validPDF), field("foo", "bar")}, 400, "unknown_field"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := newEnv()
			expectError(t, e.upload(tt.parts...), tt.status, tt.code)
			expectNoSideEffects(t, e)
		})
	}
}

func TestR4IgnoresDeclaredTypeAndName(t *testing.T) {
	t.Run("PDF named .txt is accepted", func(t *testing.T) {
		e := newEnv()
		if rec := e.upload(part{"file", "invoice.txt", validPDF}); rec.Code != http.StatusAccepted {
			t.Fatalf("status %d, want 202; body %s", rec.Code, rec.Body)
		}
	})
	t.Run("non-PDF named .pdf with application/pdf is rejected", func(t *testing.T) {
		e := newEnv()
		// multipartBody sets Content-Type application/pdf on file parts.
		expectError(t, e.upload(part{"file", "invoice.pdf", "GIF89a"}), 415, "not_pdf")
	})
	t.Run("file part without filename is accepted", func(t *testing.T) {
		e := newEnv()
		if rec := e.upload(field("file", validPDF)); rec.Code != http.StatusAccepted {
			t.Fatalf("status %d, want 202; body %s", rec.Code, rec.Body)
		}
	})
}

func TestMalformedMultipart(t *testing.T) {
	t.Run("truncated body", func(t *testing.T) {
		e := newEnv()
		body := "--XYZ\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\n%PDF-1.4" // no closing boundary
		req := httptest.NewRequest(http.MethodPost, "/documents", strings.NewReader(body))
		req.Header.Set("Content-Type", "multipart/form-data; boundary=XYZ")
		expectError(t, e.do(req), http.StatusBadRequest, "malformed_request")
		expectNoSideEffects(t, e)
	})
	t.Run("broken part header", func(t *testing.T) {
		e := newEnv()
		body := "--XYZ\r\nno colon here\r\n\r\n%PDF-1.4\r\n--XYZ--\r\n"
		req := httptest.NewRequest(http.MethodPost, "/documents", strings.NewReader(body))
		req.Header.Set("Content-Type", "multipart/form-data; boundary=XYZ")
		expectError(t, e.do(req), http.StatusBadRequest, "malformed_request")
	})
	t.Run("body without any boundary has no parts", func(t *testing.T) {
		// Go's multipart reader treats this as an empty form, so the file is missing.
		e := newEnv()
		req := httptest.NewRequest(http.MethodPost, "/documents", strings.NewReader("not multipart at all"))
		req.Header.Set("Content-Type", "multipart/form-data; boundary=XYZ")
		expectError(t, e.do(req), http.StatusBadRequest, "invalid_file_part")
	})
}

func TestCorrelationID(t *testing.T) {
	accepted := func(t *testing.T, rec *httptest.ResponseRecorder) acceptedBody {
		t.Helper()
		if rec.Code != http.StatusAccepted {
			t.Fatalf("status %d, want 202; body %s", rec.Code, rec.Body)
		}
		var b acceptedBody
		json.Unmarshal(rec.Body.Bytes(), &b)
		return b
	}

	t.Run("client value is used and lowercased", func(t *testing.T) {
		e := newEnv()
		b := accepted(t, e.upload(fileField(validPDF), field("correlation_id", "0F8FAD5B-D9CB-469F-A165-70867728950E")))
		want := "0f8fad5b-d9cb-469f-a165-70867728950e"
		if b.CorrelationID != want {
			t.Fatalf("correlation_id %q, want %q", b.CorrelationID, want)
		}
		if e.storage.correlationID != want || e.pub.correlationID != want {
			t.Errorf("not propagated: storage %q, publisher %q", e.storage.correlationID, e.pub.correlationID)
		}
	})

	t.Run("client value before the file is used", func(t *testing.T) {
		e := newEnv()
		id := correlation.New()
		if b := accepted(t, e.upload(field("correlation_id", id), fileField(validPDF))); b.CorrelationID != id {
			t.Fatalf("correlation_id %q, want %q", b.CorrelationID, id)
		}
	})

	for name, parts := range map[string][]part{
		"missing": {fileField(validPDF)},
		"empty":   {fileField(validPDF), field("correlation_id", "")},
	} {
		t.Run(name+" gets a generated UUIDv4", func(t *testing.T) {
			e := newEnv()
			b := accepted(t, e.upload(parts...))
			if _, ok := correlation.Parse(b.CorrelationID); !ok || b.CorrelationID[14] != '4' {
				t.Fatalf("correlation_id %q is not a UUIDv4", b.CorrelationID)
			}
			if e.pub.correlationID != b.CorrelationID {
				t.Errorf("publisher got %q, want %q", e.pub.correlationID, b.CorrelationID)
			}
		})
	}

	t.Run("duplicates across requests are accepted", func(t *testing.T) {
		e := newEnv()
		id := correlation.New()
		accepted(t, e.upload(fileField(validPDF), field("correlation_id", id)))
		accepted(t, e.upload(fileField(validPDF), field("correlation_id", id)))
	})
}

func TestUploadHappyPath(t *testing.T) {
	e := newEnv()
	fixed := time.Date(2026, 10, 14, 12, 0, 0, 0, time.UTC)
	e.server.now = func() time.Time { return fixed }
	id := correlation.New()

	rec := e.upload(fileField(validPDF), field("correlation_id", id))
	if rec.Code != http.StatusAccepted {
		t.Fatalf("status %d, want 202; body %s", rec.Code, rec.Body)
	}
	var b acceptedBody
	json.Unmarshal(rec.Body.Bytes(), &b)
	if _, ok := correlation.Parse(b.DocumentID); !ok {
		t.Fatalf("document_id %q is not a UUID", b.DocumentID)
	}

	if got := e.calls.list(); len(got) != 2 || got[0] != "put" || got[1] != "publish" {
		t.Fatalf("calls %v, want [put publish]", got)
	}
	wantKey := "documents/" + b.DocumentID + "/original.pdf"
	if e.storage.key != wantKey {
		t.Errorf("object key %q, want %q", e.storage.key, wantKey)
	}
	if string(e.storage.data) != validPDF {
		t.Errorf("stored data differs from upload")
	}

	var m map[string]any
	if err := json.Unmarshal(e.pub.body, &m); err != nil {
		t.Fatalf("envelope is not JSON: %s", e.pub.body)
	}
	want := map[string]any{
		"schema_version": float64(1),
		"stage":          "ingestor",
		"document_id":    b.DocumentID,
		"correlation_id": id,
		"object_key":     wantKey,
		"created_at":     "2026-10-14T12:00:00Z",
	}
	if len(m) != len(want) {
		t.Errorf("envelope has fields %v, want exactly %v", m, want)
	}
	for k, v := range want {
		if m[k] != v {
			t.Errorf("envelope %s = %v, want %v", k, m[k], v)
		}
	}
}

func TestDependencyFailures(t *testing.T) {
	t.Run("storage failure: 503 and nothing published", func(t *testing.T) {
		e := newEnv()
		e.storage.putErr = errDown
		expectError(t, e.upload(fileField(validPDF)), http.StatusServiceUnavailable, "service_unavailable")
		if got := e.calls.list(); len(got) != 1 || got[0] != "put" {
			t.Fatalf("calls %v, want [put]", got)
		}
	})
	t.Run("publish failure: 503", func(t *testing.T) {
		e := newEnv()
		e.pub.publishErr = errDown
		expectError(t, e.upload(fileField(validPDF)), http.StatusServiceUnavailable, "service_unavailable")
	})
}

func TestR7UploadTimeout(t *testing.T) {
	e := newEnv()
	srv := httptest.NewUnstartedServer(e.handler)
	srv.Config.ReadTimeout = 300 * time.Millisecond
	srv.Start()
	defer srv.Close()

	conn, err := net.Dial("tcp", srv.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	// Headers announce 1000 bytes, but only the start of the body is sent.
	io.WriteString(conn, "POST /documents HTTP/1.1\r\nHost: x\r\n"+
		"Content-Type: multipart/form-data; boundary=B\r\nContent-Length: 1000\r\n\r\n"+
		"--B\r\nContent-Disposition: form-data; name=\"file\"\r\n\r\n%PDF-")

	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	resp, err := http.ReadResponse(bufioReader(conn), nil)
	if err != nil {
		t.Fatalf("no response: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	rec := httptest.NewRecorder()
	rec.Code = resp.StatusCode
	rec.HeaderMap = resp.Header
	rec.Body = bytes.NewBuffer(body)
	expectError(t, rec, http.StatusRequestTimeout, "request_timeout")
	expectNoSideEffects(t, e)
}
