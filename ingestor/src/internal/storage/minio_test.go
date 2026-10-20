package storage

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

// TestCorrelationHeaderReachesServer uses a fake S3 endpoint to check that
// every request MinIO's client sends carries X-Correlation-ID.
func TestCorrelationHeaderReachesServer(t *testing.T) {
	var mu sync.Mutex
	var seen []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = append(seen, r.Method+" "+r.URL.Path+" "+r.Header.Get(correlation.Header))
		mu.Unlock()
		if r.URL.Query().Has("location") {
			w.Write([]byte(`<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/">us-east-1</LocationConstraint>`))
			return
		}
		w.Header().Set("ETag", `"abc"`)
	}))
	defer srv.Close()

	m, err := NewMinio(strings.TrimPrefix(srv.URL, "http://"), "key", "secret", false)
	if err != nil {
		t.Fatal(err)
	}
	id := correlation.New()
	ctx := correlation.WithState(context.Background(), &correlation.State{CorrelationID: id})
	if err := m.PutPDF(ctx, "documents/x/original.pdf", []byte("%PDF-1.4")); err != nil {
		t.Fatal(err)
	}

	mu.Lock()
	defer mu.Unlock()
	if len(seen) == 0 {
		t.Fatal("no requests reached the server")
	}
	for _, s := range seen {
		if !strings.HasSuffix(s, " "+id) {
			t.Errorf("request %q has no correlation header %s", s, id)
		}
	}
}
