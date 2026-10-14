// Package api implements the ingestor HTTP API (see ingestor/specs/api.md).
package api

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

const (
	// QueueName is the output queue (see /specs/pipeline-contracts.md).
	QueueName = "documents.ingested"

	healthTimeout     = 3 * time.Second
	dependencyTimeout = 10 * time.Second
)

// Storage stores original documents.
type Storage interface {
	PutPDF(ctx context.Context, key string, data []byte) error
	Check(ctx context.Context) error
}

// Publisher publishes messages to the output queue.
type Publisher interface {
	Publish(ctx context.Context, body []byte) error
	Check(ctx context.Context) error
}

type Server struct {
	storage   Storage
	publisher Publisher
	log       *slog.Logger
	now       func() time.Time

	healthTimeout     time.Duration
	dependencyTimeout time.Duration

	// uploads limits concurrent POST /documents requests; each holds up to
	// MaxRequestBytes in memory.
	uploads chan struct{}
}

// NewServer creates a server that handles at most maxUploads uploads at once.
func NewServer(storage Storage, publisher Publisher, log *slog.Logger, maxUploads int) *Server {
	return &Server{
		storage:           storage,
		publisher:         publisher,
		log:               log,
		uploads:           make(chan struct{}, maxUploads),
		now:               time.Now,
		healthTimeout:     healthTimeout,
		dependencyTimeout: dependencyTimeout,
	}
}

// Handler returns the HTTP handler with routing and per-request logging.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/documents", s.method(http.MethodPost, s.createDocument))
	mux.HandleFunc("/healthz", s.method(http.MethodGet, s.healthz))
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		s.fail(w, r, newError(http.StatusNotFound, "not_found", "unknown path "+r.URL.Path))
	})
	return s.withState(mux)
}

func (s *Server) method(m string, h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != m {
			w.Header().Set("Allow", m)
			s.fail(w, r, newError(http.StatusMethodNotAllowed, "method_not_allowed",
				r.Method+" is not allowed, use "+m))
			return
		}
		h(w, r)
	}
}

// withState assigns a correlation ID to every request and writes one access
// log entry when the request is done.
func (s *Server) withState(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := s.now()
		state := &correlation.State{CorrelationID: correlation.New()}
		r = r.WithContext(correlation.WithState(r.Context(), state))
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}

		next.ServeHTTP(rec, r)

		if r.URL.Path == "/healthz" && rec.status == http.StatusOK {
			return // healthchecks run every few seconds; log only failures
		}
		correlation.Logger(r.Context(), s.log).Info("request",
			"method", r.Method,
			"path", r.URL.Path,
			"status", rec.status,
			"duration_ms", s.now().Sub(start).Milliseconds())
	})
}

func (s *Server) fail(w http.ResponseWriter, r *http.Request, e *apiError) {
	log := correlation.Logger(r.Context(), s.log)
	if e.Status >= 500 && e.Code != "server_busy" {
		log.Error("request failed", "code", e.Code, "error", e.Message)
	} else {
		log.Info("request rejected", "code", e.Code, "error", e.Message)
	}
	writeError(w, e, correlation.ID(r.Context()))
}

type healthBody struct {
	Status  string   `json:"status"`
	Failing []string `json:"failing,omitempty"`
}

func (s *Server) healthz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), s.healthTimeout)
	defer cancel()

	checks := []struct {
		name  string
		check func(context.Context) error
	}{
		{"rabbitmq", s.publisher.Check},
		{"minio", s.storage.Check},
	}
	errs := make([]error, len(checks))
	done := make(chan struct{}, len(checks))
	for i, c := range checks {
		go func() {
			errs[i] = c.check(ctx)
			done <- struct{}{}
		}()
	}
	for range checks {
		<-done
	}

	var failing []string
	for i, c := range checks {
		if errs[i] != nil {
			failing = append(failing, c.name)
			correlation.Logger(r.Context(), s.log).Warn("health check failed",
				"dependency", c.name, "error", errs[i].Error())
		}
	}
	if len(failing) > 0 {
		writeJSON(w, http.StatusServiceUnavailable, healthBody{Status: "unavailable", Failing: failing})
		return
	}
	writeJSON(w, http.StatusOK, healthBody{Status: "ok"})
}

// envelope is the queue message (see /specs/pipeline-contracts.md, Message envelope).
type envelope struct {
	SchemaVersion int    `json:"schema_version"`
	Stage         string `json:"stage"`
	DocumentID    string `json:"document_id"`
	CorrelationID string `json:"correlation_id"`
	ObjectKey     string `json:"object_key"`
	CreatedAt     string `json:"created_at"`
}

type acceptedBody struct {
	DocumentID    string `json:"document_id"`
	CorrelationID string `json:"correlation_id"`
}

func (s *Server) createDocument(w http.ResponseWriter, r *http.Request) {
	state := correlation.FromContext(r.Context())

	select {
	case s.uploads <- struct{}{}:
		defer func() { <-s.uploads }()
	default:
		correlation.Logger(r.Context(), s.log).Warn(
			"request rejected before correlation_id was read, using generated correlation_id",
			"code", "server_busy")
		s.fail(w, r, newError(http.StatusServiceUnavailable, "server_busy",
			"too many concurrent uploads, retry later"))
		return
	}

	pdf, apiErr := s.readUpload(w, r, state)
	if apiErr != nil {
		s.fail(w, r, apiErr)
		return
	}

	state.DocumentID = correlation.New()
	key := "documents/" + state.DocumentID + "/original.pdf"

	ctx, cancel := context.WithTimeout(r.Context(), s.dependencyTimeout)
	defer cancel()
	if err := s.storage.PutPDF(ctx, key, pdf); err != nil {
		s.fail(w, r, newError(http.StatusServiceUnavailable, "service_unavailable",
			"object storage unavailable: "+err.Error()))
		return
	}

	body, err := json.Marshal(envelope{
		SchemaVersion: 1,
		Stage:         "ingestor",
		DocumentID:    state.DocumentID,
		CorrelationID: state.CorrelationID,
		ObjectKey:     key,
		CreatedAt:     s.now().UTC().Format(time.RFC3339Nano),
	})
	if err != nil {
		s.fail(w, r, newError(http.StatusInternalServerError, "internal_error", err.Error()))
		return
	}

	ctx, cancel = context.WithTimeout(r.Context(), s.dependencyTimeout)
	defer cancel()
	if err := s.publisher.Publish(ctx, body); err != nil {
		s.fail(w, r, newError(http.StatusServiceUnavailable, "service_unavailable",
			"queue unavailable: "+err.Error()))
		return
	}

	writeJSON(w, http.StatusAccepted, acceptedBody{
		DocumentID:    state.DocumentID,
		CorrelationID: state.CorrelationID,
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}

// Unwrap lets http.ResponseController reach the underlying writer.
func (r *statusRecorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }
