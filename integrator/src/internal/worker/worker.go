// Package worker consumes documents.fields_extracted: one message at a time, failure messages
// published with confirms, reconnect, and a pause while PostgreSQL is unavailable
// (integrator/specs/overview.md, Database unavailable).
package worker

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/correlation"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/envelope"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/processor"
)

var ReconnectWait = 5 * time.Second

type Handler interface {
	Handle(ctx context.Context, body []byte) (processor.Outcome, error)
}

type Worker struct {
	URL           string
	Handler       Handler
	DBCheck       func(ctx context.Context) error
	CheckInterval time.Duration
	Log           *slog.Logger

	Connected atomic.Bool // read by the health endpoint
	Paused    atomic.Bool // true while waiting for PostgreSQL

	mu      sync.Mutex
	ch      *amqp.Channel
	returns chan amqp.Return
}

// Run consumes until ctx is cancelled.
func (w *Worker) Run(ctx context.Context) {
	for ctx.Err() == nil {
		if w.Paused.Load() {
			w.waitForDatabase(ctx)
			continue
		}
		if err := w.consume(ctx); err != nil && ctx.Err() == nil {
			w.Log.Error("rabbitmq connection lost, reconnecting", "error", err.Error(), "wait_s", ReconnectWait.Seconds())
			sleep(ctx, ReconnectWait)
		}
	}
}

func (w *Worker) waitForDatabase(ctx context.Context) {
	if !sleep(ctx, w.CheckInterval) {
		return
	}
	checkCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	if err := w.DBCheck(checkCtx); err != nil {
		w.Log.Debug("database still unavailable", "error", err.Error())
		return
	}
	w.Log.Info("database available again, resuming")
	w.Paused.Store(false)
}

func (w *Worker) consume(ctx context.Context) error {
	conn, err := amqp.DialConfig(w.URL, amqp.Config{Dial: amqp.DefaultDial(5 * time.Second)})
	if err != nil {
		return fmt.Errorf("dial rabbitmq: %w", err)
	}
	defer conn.Close()
	defer w.Connected.Store(false)

	ch, err := conn.Channel()
	if err != nil {
		return fmt.Errorf("open channel: %w", err)
	}
	for _, q := range []string{envelope.InputQueue, envelope.FailedQueue} {
		if _, err := ch.QueueDeclarePassive(q, true, false, false, false, nil); err != nil {
			return fmt.Errorf("queue %s: %w", q, err) // created by infra; the failed declare closed the channel
		}
	}
	if err := ch.Confirm(false); err != nil {
		return fmt.Errorf("enable confirms: %w", err)
	}
	if err := ch.Qos(1, 0, false); err != nil {
		return fmt.Errorf("qos: %w", err)
	}
	w.mu.Lock()
	w.ch, w.returns = ch, ch.NotifyReturn(make(chan amqp.Return, 1))
	w.mu.Unlock()
	deliveries, err := ch.Consume(envelope.InputQueue, "", false, false, false, false, nil)
	if err != nil {
		return fmt.Errorf("consume: %w", err)
	}
	w.Connected.Store(true)
	w.Log.Info("consuming", "queue", envelope.InputQueue)

	for {
		select {
		case <-ctx.Done():
			return nil
		case d, ok := <-deliveries:
			if !ok {
				return errors.New("delivery channel closed")
			}
			// Finish the message even during shutdown, so a write is never cut off halfway.
			outcome, err := w.Handler.Handle(context.WithoutCancel(ctx), d.Body)
			if err != nil {
				return err // not acked: redelivered after reconnect
			}
			switch outcome {
			case processor.Ack:
				err = d.Ack(false)
			case processor.Reject:
				err = d.Reject(false)
			case processor.Pause:
				err = d.Nack(false, true) // back to the queue, keeps its place
				w.Paused.Store(true)
				if err == nil {
					return nil
				}
			}
			if err != nil {
				return fmt.Errorf("ack: %w", err)
			}
		}
	}
}

// Publish sends a persistent, mandatory message on the consuming channel and waits for the confirm.
func (w *Worker) Publish(ctx context.Context, queue string, body []byte, correlationID string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.ch == nil {
		return errors.New("no channel")
	}
	dc, err := w.ch.PublishWithDeferredConfirmWithContext(ctx, "", queue, true, false, amqp.Publishing{
		ContentType:   "application/json",
		DeliveryMode:  amqp.Persistent,
		CorrelationId: correlationID,
		Headers:       amqp.Table{correlation.Header: correlationID},
		Timestamp:     time.Now().UTC(),
		Body:          body,
	})
	if err != nil {
		return fmt.Errorf("publish: %w", err)
	}
	ok, err := dc.WaitContext(ctx)
	if err != nil {
		return fmt.Errorf("wait for confirm: %w", err)
	}
	if !ok {
		return errors.New("publish not confirmed by broker")
	}
	select {
	case r, open := <-w.returns:
		if open {
			return fmt.Errorf("message unroutable: %d %s", r.ReplyCode, r.ReplyText)
		}
	default:
	}
	return nil
}

// sleep waits d or until ctx ends; false if ctx ended.
func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
