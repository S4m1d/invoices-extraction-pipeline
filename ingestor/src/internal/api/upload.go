package api

import (
	"bytes"
	"errors"
	"io"
	"mime"
	"net/http"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/correlation"
)

// MaxRequestBytes is the request body limit (5 MiB).
const MaxRequestBytes = 5 << 20

var pdfSignature = []byte("%PDF-")

// readUpload applies the request restrictions (see ingestor/specs/api.md,
// Request restrictions) and returns the PDF bytes. Parts are checked in the
// order they arrive; the first violation is returned. A valid client
// correlation_id replaces the generated one in state.
func (s *Server) readUpload(w http.ResponseWriter, r *http.Request, state *correlation.State) ([]byte, *apiError) {
	u := uploadReader{server: s, state: state}
	pdf, apiErr := u.read(w, r)
	if apiErr != nil && !u.bodyRead && !u.correlationSeen {
		correlation.Logger(r.Context(), s.log).Warn(
			"request rejected before correlation_id was read, using generated correlation_id",
			"code", apiErr.Code)
	}
	return pdf, apiErr
}

type uploadReader struct {
	server *Server
	state  *correlation.State

	bodyRead        bool // all parts were read
	correlationSeen bool // a correlation_id part was processed
}

func (u *uploadReader) read(w http.ResponseWriter, r *http.Request) ([]byte, *apiError) {
	state := u.state
	// R1
	mediaType, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != "multipart/form-data" || params["boundary"] == "" {
		return nil, newError(http.StatusUnsupportedMediaType, "invalid_content_type",
			"request Content-Type must be multipart/form-data with a boundary")
	}

	// Size limit: reject early on Content-Length, otherwise stop reading at the limit.
	if r.ContentLength > MaxRequestBytes {
		return nil, newError(http.StatusRequestEntityTooLarge, "file_too_large",
			"request body exceeds 5242880 bytes")
	}
	r.Body = http.MaxBytesReader(w, r.Body, MaxRequestBytes)

	mr, err := r.MultipartReader()
	if err != nil {
		return nil, readError(err)
	}

	var pdf []byte
	fileSeen := false
	for {
		part, err := mr.NextPart()
		if errors.Is(err, io.EOF) {
			u.bodyRead = true
			break
		}
		if err != nil {
			return nil, readError(err)
		}

		switch part.FormName() {
		case "file":
			// R2
			if fileSeen {
				return nil, newError(http.StatusBadRequest, "invalid_file_part", "more than one file part")
			}
			fileSeen = true
			pdf, err = io.ReadAll(part)
			if err != nil {
				return nil, readError(err)
			}
			// R3
			if len(pdf) == 0 {
				return nil, newError(http.StatusBadRequest, "empty_file", "file is empty")
			}
			// R4
			if !bytes.HasPrefix(pdf, pdfSignature) {
				return nil, newError(http.StatusUnsupportedMediaType, "not_pdf",
					"file does not start with the PDF signature %PDF-")
			}

		case "correlation_id":
			// R5
			if u.correlationSeen {
				return nil, newError(http.StatusBadRequest, "invalid_correlation_id", "more than one correlation_id")
			}
			u.correlationSeen = true
			if part.FileName() != "" {
				correlation.Logger(r.Context(), u.server.log).Warn(
					"client correlation_id invalid, using generated correlation_id",
					"client_correlation_id", "(sent as a file)")
				return nil, newError(http.StatusBadRequest, "invalid_correlation_id", "correlation_id must be a form field, not a file")
			}
			value, err := io.ReadAll(part)
			if err != nil {
				return nil, readError(err)
			}
			if len(value) == 0 {
				continue // empty: keep the generated ID
			}
			id, ok := correlation.Parse(string(value))
			if !ok {
				correlation.Logger(r.Context(), u.server.log).Warn(
					"client correlation_id invalid, using generated correlation_id",
					"client_correlation_id", truncate(string(value), 64))
				return nil, newError(http.StatusBadRequest, "invalid_correlation_id",
					"correlation_id must be a UUID in canonical hyphenated form")
			}
			state.CorrelationID = id

		default:
			// R2: no other file parts; R6: no unknown fields
			if part.FileName() != "" {
				return nil, newError(http.StatusBadRequest, "invalid_file_part",
					"unexpected file part "+quote(part.FormName()))
			}
			return nil, newError(http.StatusBadRequest, "unknown_field",
				"unknown form field "+quote(part.FormName()))
		}
	}

	if !fileSeen {
		return nil, newError(http.StatusBadRequest, "invalid_file_part", "missing file part")
	}
	return pdf, nil
}

func quote(s string) string { return `"` + s + `"` }

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
