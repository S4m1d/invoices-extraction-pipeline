//go:build integration

// Integration tests against a real RabbitMQ. Run with:
//
//	AMQP_URL=amqp://guest:guest@localhost:5672/ go test -tags integration ./internal/queue/
package queue

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

func amqpURL(t *testing.T) string {
	t.Helper()
	u := os.Getenv("AMQP_URL")
	if u == "" {
		t.Fatal("AMQP_URL is not set")
	}
	return u
}

// testQueue declares a durable queue with a unique name and deletes it after the test.
func testQueue(t *testing.T) (name string, ch *amqp.Channel) {
	t.Helper()
	conn, err := amqp.Dial(amqpURL(t))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	ch, err = conn.Channel()
	if err != nil {
		t.Fatal(err)
	}
	name = "test.ingestor." + correlation.New()
	if _, err := ch.QueueDeclare(name, true, false, false, false, nil); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ch.QueueDelete(name, false, false, false) })
	return name, ch
}

func ctxWithID(id string) context.Context {
	return correlation.WithState(context.Background(), &correlation.State{CorrelationID: id})
}

func TestPublishConfirmed(t *testing.T) {
	q, ch := testQueue(t)
	p := NewPublisher(amqpURL(t), q)
	defer p.Close()

	id := correlation.New()
	if err := p.Publish(ctxWithID(id), []byte(`{"x":1}`)); err != nil {
		t.Fatal(err)
	}

	d, ok, err := ch.Get(q, true)
	if err != nil || !ok {
		t.Fatalf("no message in queue: ok=%v err=%v", ok, err)
	}
	if string(d.Body) != `{"x":1}` {
		t.Errorf("body %s", d.Body)
	}
	if d.DeliveryMode != amqp.Persistent {
		t.Errorf("delivery mode %d, want persistent", d.DeliveryMode)
	}
	if d.ContentType != "application/json" {
		t.Errorf("content type %q", d.ContentType)
	}
	if d.CorrelationId != id {
		t.Errorf("correlation_id property %q, want %q", d.CorrelationId, id)
	}
	if d.Headers[correlation.Header] != id {
		t.Errorf("header %v, want %q", d.Headers[correlation.Header], id)
	}
}

func TestPublishToMissingQueueFails(t *testing.T) {
	p := NewPublisher(amqpURL(t), "test.ingestor.missing."+correlation.New())
	defer p.Close()
	if err := p.Publish(context.Background(), []byte("{}")); err == nil {
		t.Fatal("expected error for missing queue")
	}
}

func TestPublishUnroutableAfterQueueDeleted(t *testing.T) {
	q, ch := testQueue(t)
	p := NewPublisher(amqpURL(t), q)
	defer p.Close()
	if err := p.Publish(context.Background(), []byte("{}")); err != nil {
		t.Fatal(err)
	}
	if _, err := ch.QueueDelete(q, false, false, false); err != nil {
		t.Fatal(err)
	}
	err := p.Publish(context.Background(), []byte("{}"))
	if err == nil || !strings.Contains(err.Error(), "NO_ROUTE") {
		t.Fatalf("error %v, want NO_ROUTE", err)
	}
}

func TestReconnectAfterConnectionLoss(t *testing.T) {
	q, ch := testQueue(t)
	p := NewPublisher(amqpURL(t), q)
	defer p.Close()
	if err := p.Publish(context.Background(), []byte("1")); err != nil {
		t.Fatal(err)
	}

	p.mu.Lock()
	p.conn.Close() // simulate the broker dropping the connection
	p.mu.Unlock()

	if err := p.Publish(context.Background(), []byte("2")); err != nil {
		t.Fatalf("publish after connection loss: %v", err)
	}
	time.Sleep(100 * time.Millisecond)
	q2, _ := ch.QueueDeclarePassive(q, true, false, false, false, nil)
	if q2.Messages != 2 {
		t.Fatalf("%d messages in queue, want 2", q2.Messages)
	}
}

func TestCheck(t *testing.T) {
	q, _ := testQueue(t)
	p := NewPublisher(amqpURL(t), q)
	defer p.Close()
	if err := p.Check(context.Background()); err != nil {
		t.Fatalf("check with existing queue: %v", err)
	}

	missing := NewPublisher(amqpURL(t), "test.ingestor.missing."+correlation.New())
	defer missing.Close()
	if err := missing.Check(context.Background()); err == nil {
		t.Fatal("check with missing queue: want error")
	}

	down := NewPublisher("amqp://guest:guest@127.0.0.1:1/", q)
	defer down.Close()
	if err := down.Check(context.Background()); err == nil {
		t.Fatal("check with unreachable broker: want error")
	}
}
