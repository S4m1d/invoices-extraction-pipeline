//go:build smoke

// Package smoke checks a running ingestor against real RabbitMQ and MinIO.
// The queue documents.ingested and the bucket invoices must already exist.
//
//	INGESTOR_URL=http://localhost:8080 AMQP_URL=... MINIO_ENDPOINT=... \
//	MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... \
//	go test -tags smoke -count=1 -v ./smoke/
package smoke

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"regexp"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	queueName  = "documents.ingested"
	bucketName = "invoices"
)

var uuidRE = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// minimalPDF is a tiny but valid one-page PDF.
const minimalPDF = "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" +
	"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" +
	"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n" +
	"trailer<</Root 1 0 R>>\n%%EOF\n"

func env(t *testing.T, name string) string {
	t.Helper()
	v := os.Getenv(name)
	if v == "" {
		t.Fatalf("%s is not set", name)
	}
	return v
}

func upload(t *testing.T, fields map[string]string, file []byte) (*http.Response, map[string]any) {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, err := mw.CreateFormFile("file", "invoice.pdf")
	if err != nil {
		t.Fatal(err)
	}
	fw.Write(file)
	for k, v := range fields {
		mw.WriteField(k, v)
	}
	mw.Close()

	resp, err := http.Post(env(t, "INGESTOR_URL")+"/documents", mw.FormDataContentType(), &buf)
	if err != nil {
		t.Fatalf("POST /documents: %v", err)
	}
	defer resp.Body.Close()
	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return resp, body
}

// takeMessage gets messages from the queue until it finds the one for
// documentID; other messages are requeued.
func takeMessage(t *testing.T, documentID string) amqp.Delivery {
	t.Helper()
	conn, err := amqp.Dial(env(t, "AMQP_URL"))
	if err != nil {
		t.Fatalf("dial rabbitmq: %v", err)
	}
	defer conn.Close()
	ch, err := conn.Channel()
	if err != nil {
		t.Fatal(err)
	}
	defer ch.Close()

	var requeue []amqp.Delivery
	defer func() {
		for _, d := range requeue {
			d.Nack(false, true)
		}
	}()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		d, ok, err := ch.Get(queueName, false)
		if err != nil {
			t.Fatalf("get from %s: %v", queueName, err)
		}
		if !ok {
			time.Sleep(100 * time.Millisecond)
			continue
		}
		var m map[string]any
		if json.Unmarshal(d.Body, &m) == nil && m["document_id"] == documentID {
			d.Ack(false)
			return d
		}
		requeue = append(requeue, d)
	}
	t.Fatalf("no message for document %s in %s", documentID, queueName)
	return amqp.Delivery{}
}

func TestSmoke(t *testing.T) {
	t.Run("healthz", func(t *testing.T) {
		resp, err := http.Get(env(t, "INGESTOR_URL") + "/healthz")
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("status %d, want 200", resp.StatusCode)
		}
	})

	t.Run("upload stores the PDF and publishes the envelope", func(t *testing.T) {
		correlationID := uuid.NewString()
		resp, body := upload(t, map[string]string{"correlation_id": correlationID}, []byte(minimalPDF))
		if resp.StatusCode != http.StatusAccepted {
			t.Fatalf("status %d, want 202, body %v", resp.StatusCode, body)
		}
		documentID, _ := body["document_id"].(string)
		if !uuidRE.MatchString(documentID) {
			t.Fatalf("document_id %q is not a UUID", documentID)
		}
		if body["correlation_id"] != correlationID {
			t.Fatalf("correlation_id %v, want %s", body["correlation_id"], correlationID)
		}

		key := "documents/" + documentID + "/original.pdf"
		mc, err := minio.New(env(t, "MINIO_ENDPOINT"), &minio.Options{
			Creds: credentials.NewStaticV4(env(t, "MINIO_ACCESS_KEY"), env(t, "MINIO_SECRET_KEY"), ""),
		})
		if err != nil {
			t.Fatal(err)
		}
		obj, err := mc.GetObject(context.Background(), bucketName, key, minio.GetObjectOptions{})
		if err != nil {
			t.Fatal(err)
		}
		stored, err := io.ReadAll(obj)
		if err != nil {
			t.Fatalf("read %s: %v", key, err)
		}
		if string(stored) != minimalPDF {
			t.Fatalf("stored object differs from upload")
		}

		d := takeMessage(t, documentID)
		var m map[string]any
		json.Unmarshal(d.Body, &m)
		want := map[string]any{
			"schema_version": float64(1),
			"stage":          "ingestor",
			"document_id":    documentID,
			"correlation_id": correlationID,
			"object_key":     key,
		}
		for k, v := range want {
			if m[k] != v {
				t.Errorf("envelope %s = %v, want %v", k, m[k], v)
			}
		}
		if _, err := time.Parse(time.RFC3339, m["created_at"].(string)); err != nil {
			t.Errorf("created_at %v is not RFC 3339", m["created_at"])
		}
		if d.DeliveryMode != amqp.Persistent {
			t.Errorf("delivery mode %d, want persistent", d.DeliveryMode)
		}
	})

	t.Run("upload without correlation_id gets a generated one", func(t *testing.T) {
		resp, body := upload(t, nil, []byte(minimalPDF))
		if resp.StatusCode != http.StatusAccepted {
			t.Fatalf("status %d, want 202, body %v", resp.StatusCode, body)
		}
		id, _ := body["correlation_id"].(string)
		if !uuidRE.MatchString(id) {
			t.Fatalf("correlation_id %q is not a UUID", id)
		}
		takeMessage(t, body["document_id"].(string))
	})

	t.Run("non-PDF is rejected", func(t *testing.T) {
		resp, body := upload(t, nil, []byte("hello"))
		if resp.StatusCode != http.StatusUnsupportedMediaType {
			t.Fatalf("status %d, want 415", resp.StatusCode)
		}
		if e, _ := body["error"].(map[string]any); e["code"] != "not_pdf" {
			t.Fatalf("error %v, want code not_pdf", body["error"])
		}
	})
}
