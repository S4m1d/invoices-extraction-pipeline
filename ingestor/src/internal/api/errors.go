package api

import (
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
)

// apiError is a client-visible error (see ingestor/specs/api.md, Error response body).
type apiError struct {
	Status  int
	Code    string
	Message string
}

func (e *apiError) Error() string { return e.Code + ": " + e.Message }

func newError(status int, code, message string) *apiError {
	return &apiError{Status: status, Code: code, Message: message}
}

// readError classifies an error that occurred while reading the request body.
func readError(err error) *apiError {
	var tooLarge *http.MaxBytesError
	if errors.As(err, &tooLarge) {
		return newError(http.StatusRequestEntityTooLarge, "file_too_large",
			"request body exceeds 5242880 bytes")
	}
	var netErr net.Error
	if errors.Is(err, os.ErrDeadlineExceeded) || (errors.As(err, &netErr) && netErr.Timeout()) {
		return newError(http.StatusRequestTimeout, "request_timeout",
			"upload did not finish within 30 s")
	}
	return newError(http.StatusBadRequest, "malformed_request", "malformed multipart body: "+err.Error())
}

type errorBody struct {
	Error struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
	CorrelationID string `json:"correlation_id"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, e *apiError, correlationID string) {
	var b errorBody
	b.Error.Code = e.Code
	b.Error.Message = e.Message
	b.CorrelationID = correlationID
	writeJSON(w, e.Status, b)
}
