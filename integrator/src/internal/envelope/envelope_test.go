package envelope

import (
	"encoding/json"
	"errors"
	"testing"
	"time"
)

const (
	doc  = "0f8fad5b-d9cb-469f-a165-70867728950e"
	corr = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
)

func msg(changes map[string]any) []byte {
	m := map[string]any{"schema_version": 1, "stage": "fields-extractor", "document_id": doc, "correlation_id": corr,
		"object_key": "documents/" + doc + "/extracted.json", "created_at": "2026-10-16T12:00:00.5Z"}
	for k, v := range changes {
		if v == nil {
			delete(m, k)
		} else {
			m[k] = v
		}
	}
	b, _ := json.Marshal(m)
	return b
}

func TestValid(t *testing.T) {
	e, err := Parse(msg(nil))
	if err != nil {
		t.Fatal(err)
	}
	if e != (Envelope{doc, corr, "documents/" + doc + "/extracted.json"}) {
		t.Fatalf("%+v", e)
	}
	if _, err := Parse(msg(map[string]any{"created_at": "2026-10-16T12:00:00+02:00"})); err != nil {
		t.Fatal(err)
	}
}

func TestInvalid(t *testing.T) {
	cases := map[string][]byte{
		"not JSON":           []byte("nope"),
		"missing field":      msg(map[string]any{"correlation_id": nil}),
		"extra field":        msg(map[string]any{"extra": 1}),
		"schema_version 2":   msg(map[string]any{"schema_version": 2}),
		"schema_version str": msg(map[string]any{"schema_version": "1"}),
		"wrong stage":        msg(map[string]any{"stage": "text-extractor"}),
		"uppercase uuid":     msg(map[string]any{"document_id": "0F8FAD5B-D9CB-469F-A165-70867728950E"}),
		"bad correlation_id": msg(map[string]any{"correlation_id": "run-42"}),
		"key of other doc":   msg(map[string]any{"object_key": "documents/" + corr + "/extracted.json"}),
		"key of other file":  msg(map[string]any{"object_key": "documents/" + doc + "/layout.md"}),
		"date only":          msg(map[string]any{"created_at": "2026-10-16"}),
		"invalid month":      msg(map[string]any{"created_at": "2026-13-16T12:00:00Z"}),
		"created_at number":  msg(map[string]any{"created_at": 5}),
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := Parse(body); !errors.Is(err, ErrInvalid) {
				t.Fatalf("err %v, want ErrInvalid", err)
			}
		})
	}
}

func TestFailure(t *testing.T) {
	var m map[string]any
	json.Unmarshal(Failure(Envelope{doc, corr, "k"}, "duplicate_document", "already stored",
		time.Date(2026, 10, 16, 12, 0, 0, 0, time.FixedZone("x", 3600))), &m)
	want := map[string]any{"schema_version": float64(1), "stage": "integrator", "document_id": doc,
		"correlation_id": corr, "error_code": "duplicate_document", "error_message": "already stored",
		"created_at": "2026-10-16T11:00:00Z"}
	if len(m) != len(want) {
		t.Fatalf("%v", m)
	}
	for k, v := range want {
		if m[k] != v {
			t.Errorf("%s = %v, want %v", k, m[k], v)
		}
	}
}
