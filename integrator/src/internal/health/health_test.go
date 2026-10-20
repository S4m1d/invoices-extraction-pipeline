package health

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func serve(h http.Handler, path string) (int, map[string]any) {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
	var m map[string]any
	json.Unmarshal(rec.Body.Bytes(), &m)
	return rec.Code, m
}

func TestStatus(t *testing.T) {
	down := map[string]bool{}
	check := func(name string) Check {
		return Check{Name: name, Check: func(context.Context) error {
			if down[name] {
				return errors.New(name + " down")
			}
			return nil
		}}
	}
	h := Handler([]Check{check("rabbitmq"), check("minio"), check("postgres")}, slog.New(slog.DiscardHandler))
	if code, body := serve(h, "/healthz"); code != 200 || body["status"] != "ok" {
		t.Fatalf("%d %v", code, body)
	}
	down["postgres"], down["rabbitmq"] = true, true
	code, body := serve(h, "/healthz")
	if code != 503 || body["status"] != "unavailable" || len(body["failing"].([]any)) != 2 ||
		body["failing"].([]any)[0] != "rabbitmq" || body["failing"].([]any)[1] != "postgres" {
		t.Fatalf("%d %v", code, body)
	}
	if errs := body["errors"].(map[string]any); errs["postgres"] != "postgres down" || errs["rabbitmq"] != "rabbitmq down" {
		t.Fatalf("errors %v", body["errors"])
	}
	if code, _ := serve(h, "/other"); code != 404 {
		t.Fatalf("unknown path: %d", code)
	}
}

func TestLogsOnlyOnChange(t *testing.T) {
	var buf bytes.Buffer
	down := true
	h := Handler([]Check{{Name: "postgres", Check: func(context.Context) error {
		if down {
			return errors.New("connection refused")
		}
		return nil
	}}}, slog.New(slog.NewJSONHandler(&buf, nil)))
	for range 5 {
		serve(h, "/healthz")
	}
	down = false
	for range 3 {
		serve(h, "/healthz")
	}
	lines := strings.Split(strings.TrimSpace(buf.String()), "\n")
	if len(lines) != 2 || !strings.Contains(lines[0], "health check failed") || !strings.Contains(lines[0], "connection refused") ||
		!strings.Contains(lines[1], "health check ok again") {
		t.Fatalf("logs:\n%s", buf.String())
	}
}
