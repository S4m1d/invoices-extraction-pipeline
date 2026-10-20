package config

import (
	"log/slog"
	"strings"
	"testing"
	"time"
)

var required = map[string]string{
	"AMQP_URL": "amqp://guest:guest@localhost:5672/", "MINIO_ENDPOINT": "localhost:9000", "MINIO_ACCESS_KEY": "k",
	"MINIO_SECRET_KEY": "s", "DATABASE_URL": "postgres://integrator:x@localhost:5432/invoices",
}

func getenv(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

func TestDefaults(t *testing.T) {
	c, err := Load(getenv(required))
	if err != nil {
		t.Fatal(err)
	}
	if c.HTTPAddr != ":8080" || c.MinioUseSSL || c.DBCheckInterval != 10*time.Second || c.LogLevel != slog.LevelInfo {
		t.Fatalf("%+v", c)
	}
}

func TestOverrides(t *testing.T) {
	m := map[string]string{"INTEGRATOR_HTTP_ADDR": ":9", "MINIO_USE_SSL": "true", "INTEGRATOR_DB_CHECK_INTERVAL": "2.5", "LOG_LEVEL": "debug"}
	for k, v := range required {
		m[k] = v
	}
	c, err := Load(getenv(m))
	if err != nil {
		t.Fatal(err)
	}
	if c.HTTPAddr != ":9" || !c.MinioUseSSL || c.DBCheckInterval != 2500*time.Millisecond || c.LogLevel != slog.LevelDebug {
		t.Fatalf("%+v", c)
	}
}

func TestReportsAllProblems(t *testing.T) {
	_, err := Load(getenv(map[string]string{"MINIO_USE_SSL": "maybe", "INTEGRATOR_DB_CHECK_INTERVAL": "0", "LOG_LEVEL": "loud"}))
	if err == nil {
		t.Fatal("want error")
	}
	for _, name := range []string{"AMQP_URL", "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "DATABASE_URL",
		"MINIO_USE_SSL", "INTEGRATOR_DB_CHECK_INTERVAL", "LOG_LEVEL"} {
		if !strings.Contains(err.Error(), name) {
			t.Errorf("%q does not mention %s", err, name)
		}
	}
}
