// Package processor handles one message (integrator/specs/overview.md, Processing).
package processor

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/correlation"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/db"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/envelope"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/extraction"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/storage"
)

// Outcome tells the worker what to do with the message.
type Outcome int

const (
	Ack    Outcome = iota
	Reject         // without requeue: dead-lettered
	Pause          // database unavailable: requeue and pause (spec: Database unavailable)
)

// Storage waits 1 s and then 5 s between its 3 attempts.
var RetryWaits = []time.Duration{time.Second, 5 * time.Second}

type Storage interface {
	Get(ctx context.Context, key string) (storage.Object, error)
}

type Store interface {
	Write(ctx context.Context, r db.Record) error
}

// Publisher publishes persistently and waits for the broker's confirm.
type Publisher interface {
	Publish(ctx context.Context, queue string, body []byte, correlationID string) error
}

type Processor struct {
	Storage   Storage
	Store     Store
	Publisher Publisher
	Log       *slog.Logger
	Sleep     func(time.Duration)
	Now       func() time.Time
}

func New(s Storage, st Store, p Publisher, log *slog.Logger) *Processor {
	return &Processor{Storage: s, Store: st, Publisher: p, Log: log, Sleep: time.Sleep, Now: time.Now}
}

// Handle processes one message. A returned error is a RabbitMQ failure: the caller must not ack.
func (p *Processor) Handle(ctx context.Context, body []byte) (Outcome, error) {
	env, err := envelope.Parse(body)
	if err != nil {
		p.Log.Warn("invalid message, rejecting to DLQ", "error", err.Error())
		return Reject, nil
	}
	state := &correlation.State{CorrelationID: env.CorrelationID, DocumentID: env.DocumentID}
	ctx = correlation.WithState(ctx, state)
	log := correlation.Logger(ctx, p.Log)
	start := p.Now()

	outcome, result, err := p.process(ctx, env, log)
	if err != nil {
		return outcome, err
	}
	if result != nil { // one summary entry per processed message (spec: logging)
		log.Info("message processed", append(result, "duration_ms", p.Now().Sub(start).Milliseconds())...)
	}
	return outcome, nil
}

func (p *Processor) process(ctx context.Context, env envelope.Envelope, log *slog.Logger) (Outcome, []any, error) {
	obj, err := p.get(ctx, env.ObjectKey, log)
	if err != nil {
		if errors.Is(err, storage.ErrNotFound) {
			return p.fail(ctx, env, log, "object_not_found", err)
		}
		return p.fail(ctx, env, log, "storage_unavailable", err)
	}
	inv, err := extraction.Decode(obj.Data)
	if err != nil {
		return p.fail(ctx, env, log, "invalid_extraction", err)
	}
	rec := db.Record{
		DocumentID: env.DocumentID, CorrelationID: env.CorrelationID, Invoice: inv,
		ExtractionModel: optional(obj.Metadata["model"]), PromptVersion: optional(obj.Metadata["prompt-version"]),
	}
	err = p.Store.Write(ctx, rec)
	switch {
	case err == nil:
		return Ack, []any{"result", "ok", "lines", len(inv.Lines)}, nil
	case errors.Is(err, db.ErrDuplicate):
		return p.fail(ctx, env, log, "duplicate_document", err)
	case errors.Is(err, db.ErrRejected):
		return p.fail(ctx, env, log, "database_rejected", err)
	case errors.Is(err, db.ErrUnavailable):
		log.Error("database unavailable, pausing", "error", err.Error())
		return Pause, nil, nil
	default:
		log.Error("unexpected error", "error", err.Error())
		return p.fail(ctx, env, log, "internal_error", err)
	}
}

func (p *Processor) get(ctx context.Context, key string, log *slog.Logger) (storage.Object, error) {
	for attempt := 1; ; attempt++ {
		obj, err := p.Storage.Get(ctx, key)
		if err == nil || errors.Is(err, storage.ErrNotFound) || attempt > len(RetryWaits) {
			return obj, err
		}
		wait := RetryWaits[attempt-1]
		log.Warn("storage unavailable, retrying", "attempt", attempt, "wait_s", wait.Seconds(), "error", err.Error())
		p.Sleep(wait)
	}
}

func (p *Processor) fail(ctx context.Context, env envelope.Envelope, log *slog.Logger, code string, cause error) (Outcome, []any, error) {
	log.Warn("document failed", "error_code", code, "error", cause.Error())
	body := envelope.Failure(env, code, cause.Error(), p.Now())
	if err := p.Publisher.Publish(ctx, envelope.FailedQueue, body, env.CorrelationID); err != nil {
		return Ack, nil, err
	}
	return Ack, []any{"result", "failed", "error_code", code}, nil
}

func optional(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
