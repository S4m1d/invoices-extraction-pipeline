//go:build integration

// Integration tests against a real MinIO with the bucket "invoices". Run with:
//
//	MINIO_ENDPOINT=localhost:9000 MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... \
//	go test -tags integration ./internal/storage/
package storage

import (
	"context"
	"io"
	"os"
	"testing"
	"time"

	"github.com/minio/minio-go/v7"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

func newTestMinio(t *testing.T) *Minio {
	t.Helper()
	get := func(name string) string {
		v := os.Getenv(name)
		if v == "" {
			t.Fatalf("%s is not set", name)
		}
		return v
	}
	m, err := NewMinio(get("MINIO_ENDPOINT"), get("MINIO_ACCESS_KEY"), get("MINIO_SECRET_KEY"), false)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func TestPutPDF(t *testing.T) {
	m := newTestMinio(t)
	ctx := context.Background()
	key := "test/ingestor/" + correlation.New() + "/original.pdf"
	t.Cleanup(func() { m.client.RemoveObject(ctx, Bucket, key, minio.RemoveObjectOptions{}) })

	data := []byte("%PDF-1.4 integration")
	if err := m.PutPDF(ctx, key, data); err != nil {
		t.Fatal(err)
	}

	obj, err := m.client.GetObject(ctx, Bucket, key, minio.GetObjectOptions{})
	if err != nil {
		t.Fatal(err)
	}
	got, err := io.ReadAll(obj)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(data) {
		t.Errorf("stored %q, want %q", got, data)
	}
	info, _ := obj.Stat()
	if info.ContentType != "application/pdf" {
		t.Errorf("content type %q, want application/pdf", info.ContentType)
	}
}

func TestCheck(t *testing.T) {
	m := newTestMinio(t)
	if err := m.Check(context.Background()); err != nil {
		t.Fatalf("check with bucket %s: %v", Bucket, err)
	}

	m.bucket = "missing-" + correlation.New()
	if err := m.Check(context.Background()); err == nil {
		t.Fatal("check with missing bucket: want error")
	}

	down, _ := NewMinio("127.0.0.1:1", "key", "secret", false)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := down.Check(ctx); err == nil {
		t.Fatal("check with unreachable MinIO: want error")
	}
}
