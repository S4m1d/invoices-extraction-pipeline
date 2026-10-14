// Package correlation carries the per-request correlation ID and document ID
// through a context, and propagates the correlation ID to logs and outgoing
// HTTP calls (see /specs/pipeline-contracts.md, Correlation ID).
package correlation

import (
	"context"
	"log/slog"
	"net/http"
	"regexp"
	"strings"

	"github.com/google/uuid"
)

// Header is the HTTP header that carries the correlation ID on outgoing calls.
const Header = "X-Correlation-ID"

var canonicalUUID = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

// New generates a correlation ID (UUIDv4).
func New() string { return uuid.NewString() }

// Parse validates a client-supplied ID in canonical hyphenated form and
// returns it in lowercase.
func Parse(s string) (string, bool) {
	if !canonicalUUID.MatchString(s) {
		return "", false
	}
	return strings.ToLower(s), true
}

// State holds the identifiers of one request. Fields are set while the
// request is handled, so readers always see the current values.
type State struct {
	CorrelationID string
	DocumentID    string
}

type stateKey struct{}

// WithState attaches s to ctx.
func WithState(ctx context.Context, s *State) context.Context {
	return context.WithValue(ctx, stateKey{}, s)
}

// FromContext returns the request state, or nil if there is none.
func FromContext(ctx context.Context) *State {
	s, _ := ctx.Value(stateKey{}).(*State)
	return s
}

// ID returns the correlation ID in ctx, or "" if there is none.
func ID(ctx context.Context) string {
	if s := FromContext(ctx); s != nil {
		return s.CorrelationID
	}
	return ""
}

// Logger returns base with correlation_id and document_id (when known) added.
func Logger(ctx context.Context, base *slog.Logger) *slog.Logger {
	s := FromContext(ctx)
	if s == nil {
		return base
	}
	l := base.With("correlation_id", s.CorrelationID)
	if s.DocumentID != "" {
		l = l.With("document_id", s.DocumentID)
	}
	return l
}

// Transport adds the correlation ID from the request context to outgoing
// HTTP requests.
type Transport struct {
	Base http.RoundTripper
}

func (t *Transport) RoundTrip(req *http.Request) (*http.Response, error) {
	if id := ID(req.Context()); id != "" {
		req = req.Clone(req.Context())
		req.Header.Set(Header, id)
	}
	return t.Base.RoundTrip(req)
}
