// Package envelope validates input messages and builds failure messages (see /specs/pipeline-contracts.md).
package envelope

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"time"
)

const (
	Stage       = "integrator"
	InputStage  = "fields-extractor"
	InputQueue  = "documents.fields_extracted"
	FailedQueue = "documents.failed"
)

var (
	ErrInvalid = errors.New("invalid message")
	uuidRE     = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	timeRE     = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$`)
	fields     = []string{"correlation_id", "created_at", "document_id", "object_key", "schema_version", "stage"}
)

type Envelope struct {
	DocumentID    string
	CorrelationID string
	ObjectKey     string
}

func ExtractedKey(documentID string) string { return "documents/" + documentID + "/extracted.json" }

// Parse validates a message from the fields-extractor. Errors wrap ErrInvalid.
func Parse(body []byte) (Envelope, error) {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(body, &m); err != nil {
		return Envelope{}, fmt.Errorf("%w: not a JSON object: %v", ErrInvalid, err)
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if fmt.Sprint(keys) != fmt.Sprint(fields) {
		return Envelope{}, fmt.Errorf("%w: fields %v, want %v", ErrInvalid, keys, fields)
	}
	if string(m["schema_version"]) != "1" { // the JSON number 1, not "1" or 1.0
		return Envelope{}, fmt.Errorf("%w: schema_version %s, want 1", ErrInvalid, m["schema_version"])
	}
	str := func(k string) (string, error) {
		var s string
		if err := json.Unmarshal(m[k], &s); err != nil {
			return "", fmt.Errorf("%w: %s is not a string", ErrInvalid, k)
		}
		return s, nil
	}
	vals := map[string]string{}
	for _, k := range []string{"stage", "document_id", "correlation_id", "object_key", "created_at"} {
		v, err := str(k)
		if err != nil {
			return Envelope{}, err
		}
		vals[k] = v
	}
	switch {
	case vals["stage"] != InputStage:
		return Envelope{}, fmt.Errorf("%w: stage %q, want %q", ErrInvalid, vals["stage"], InputStage)
	case !uuidRE.MatchString(vals["document_id"]):
		return Envelope{}, fmt.Errorf("%w: document_id %q is not a lowercase UUID", ErrInvalid, vals["document_id"])
	case !uuidRE.MatchString(vals["correlation_id"]):
		return Envelope{}, fmt.Errorf("%w: correlation_id %q is not a lowercase UUID", ErrInvalid, vals["correlation_id"])
	case vals["object_key"] != ExtractedKey(vals["document_id"]):
		return Envelope{}, fmt.Errorf("%w: object_key %q does not match document_id", ErrInvalid, vals["object_key"])
	case !timeRE.MatchString(vals["created_at"]):
		return Envelope{}, fmt.Errorf("%w: created_at %q is not RFC 3339", ErrInvalid, vals["created_at"])
	}
	if _, err := time.Parse(time.RFC3339Nano, vals["created_at"]); err != nil {
		return Envelope{}, fmt.Errorf("%w: created_at %q is not RFC 3339", ErrInvalid, vals["created_at"])
	}
	return Envelope{DocumentID: vals["document_id"], CorrelationID: vals["correlation_id"], ObjectKey: vals["object_key"]}, nil
}

// Failure builds a failure message (contract: Failures).
func Failure(e Envelope, code, message string, now time.Time) []byte {
	b, _ := json.Marshal(map[string]any{
		"schema_version": 1,
		"stage":          Stage,
		"document_id":    e.DocumentID,
		"correlation_id": e.CorrelationID,
		"error_code":     code,
		"error_message":  message,
		"created_at":     now.UTC().Format(time.RFC3339Nano),
	})
	return b
}
