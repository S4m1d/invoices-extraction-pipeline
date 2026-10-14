package config

import (
	"log/slog"
	"strings"
	"testing"
)

func setRequired(t *testing.T) {
	t.Setenv("AMQP_URL", "amqp://guest:guest@localhost:5672/")
	t.Setenv("MINIO_ENDPOINT", "localhost:9000")
	t.Setenv("MINIO_ACCESS_KEY", "key")
	t.Setenv("MINIO_SECRET_KEY", "secret")
}

func TestLoadDefaults(t *testing.T) {
	setRequired(t)
	for _, name := range []string{"INGESTOR_HTTP_ADDR", "MINIO_USE_SSL", "LOG_LEVEL"} {
		t.Setenv(name, "")
	}
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if c.HTTPAddr != ":8080" || c.MinioUseSSL || c.LogLevel != slog.LevelInfo {
		t.Fatalf("defaults: %+v", c)
	}
	if c.AMQPURL == "" || c.MinioEndpoint == "" || c.MinioAccessKey == "" || c.MinioSecretKey == "" {
		t.Fatalf("required values not loaded: %+v", c)
	}
}

func TestLoadOverrides(t *testing.T) {
	setRequired(t)
	t.Setenv("INGESTOR_HTTP_ADDR", ":9999")
	t.Setenv("MINIO_USE_SSL", "true")
	t.Setenv("LOG_LEVEL", "debug")
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if c.HTTPAddr != ":9999" || !c.MinioUseSSL || c.LogLevel != slog.LevelDebug {
		t.Fatalf("overrides: %+v", c)
	}
}

func TestLoadReportsAllProblems(t *testing.T) {
	for _, name := range []string{"AMQP_URL", "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"} {
		t.Setenv(name, "")
	}
	t.Setenv("MINIO_USE_SSL", "maybe")
	t.Setenv("LOG_LEVEL", "loud")
	_, err := Load()
	if err == nil {
		t.Fatal("expected error")
	}
	for _, name := range []string{"AMQP_URL", "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_USE_SSL", "LOG_LEVEL"} {
		if !strings.Contains(err.Error(), name) {
			t.Errorf("error %q does not mention %s", err, name)
		}
	}
}
