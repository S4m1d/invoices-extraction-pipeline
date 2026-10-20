// Package queue publishes messages to RabbitMQ with publisher confirms.
package queue

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

const dialTimeout = 5 * time.Second

// Publisher publishes to one queue on the default exchange. It connects
// lazily and reconnects on the next call after a failure. Publishes are
// serialized so that returns can be matched to the message that caused them.
type Publisher struct {
	url   string
	queue string

	mu      sync.Mutex
	conn    *amqp.Connection
	ch      *amqp.Channel
	returns chan amqp.Return
}

func NewPublisher(url, queue string) *Publisher {
	return &Publisher{url: url, queue: queue}
}

// Publish sends body as a persistent, mandatory message and waits for the
// broker to confirm it. The correlation ID from ctx is set on the message.
func (p *Publisher) Publish(ctx context.Context, body []byte) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	if err := p.connect(ctx); err != nil {
		return err
	}

	id := correlation.ID(ctx)
	dc, err := p.ch.PublishWithDeferredConfirmWithContext(ctx, "", p.queue, true, false, amqp.Publishing{
		ContentType:   "application/json",
		DeliveryMode:  amqp.Persistent,
		CorrelationId: id,
		Headers:       amqp.Table{correlation.Header: id},
		Timestamp:     time.Now().UTC(),
		Body:          body,
	})
	if err != nil {
		p.reset()
		return fmt.Errorf("publish: %w", err)
	}

	acked, err := dc.WaitContext(ctx)
	if err != nil {
		p.reset()
		return fmt.Errorf("wait for confirm: %w", err)
	}
	if !acked {
		p.reset()
		return errors.New("publish not confirmed by broker")
	}

	// The broker sends basic.return before basic.ack, and the client
	// dispatches them in order, so a return is already buffered here.
	select {
	case r, ok := <-p.returns:
		if ok {
			return fmt.Errorf("message unroutable: %d %s", r.ReplyCode, r.ReplyText)
		}
	default:
	}
	return nil
}

// Check reports whether RabbitMQ is reachable and the queue exists.
func (p *Publisher) Check(ctx context.Context) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	if err := p.connect(ctx); err != nil {
		return err
	}
	return p.queueExists()
}

// Close closes the connection.
func (p *Publisher) Close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.reset()
}

// connect opens the connection and confirm-mode channel if needed. Caller holds mu.
func (p *Publisher) connect(ctx context.Context) error {
	if p.conn != nil && !p.conn.IsClosed() && p.ch != nil && !p.ch.IsClosed() {
		return nil
	}
	p.reset()

	timeout := dialTimeout
	if deadline, ok := ctx.Deadline(); ok && time.Until(deadline) < timeout {
		timeout = time.Until(deadline)
	}
	conn, err := amqp.DialConfig(p.url, amqp.Config{Dial: amqp.DefaultDial(timeout)})
	if err != nil {
		return fmt.Errorf("dial rabbitmq: %w", err)
	}
	ch, err := conn.Channel()
	if err != nil {
		conn.Close()
		return fmt.Errorf("open channel: %w", err)
	}
	if err := ch.Confirm(false); err != nil {
		conn.Close()
		return fmt.Errorf("enable confirms: %w", err)
	}
	p.conn, p.ch = conn, ch
	p.returns = ch.NotifyReturn(make(chan amqp.Return, 1))

	if err := p.queueExists(); err != nil {
		p.reset()
		return err
	}
	return nil
}

// queueExists declares the queue passively on a throwaway channel, because a
// failed passive declare closes the channel it runs on. Caller holds mu.
func (p *Publisher) queueExists() error {
	ch, err := p.conn.Channel()
	if err != nil {
		return fmt.Errorf("open channel: %w", err)
	}
	defer ch.Close()
	if _, err := ch.QueueDeclarePassive(p.queue, true, false, false, false, nil); err != nil {
		return fmt.Errorf("queue %s: %w", p.queue, err)
	}
	return nil
}

// reset drops the connection so the next call reconnects. Caller holds mu.
func (p *Publisher) reset() {
	if p.conn != nil {
		p.conn.Close()
	}
	p.conn, p.ch, p.returns = nil, nil, nil
}
