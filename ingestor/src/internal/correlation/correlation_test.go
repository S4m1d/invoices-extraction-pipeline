package correlation

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestParse(t *testing.T) {
	tests := []struct {
		in     string
		want   string
		wantOK bool
	}{
		{"0f8fad5b-d9cb-469f-a165-70867728950e", "0f8fad5b-d9cb-469f-a165-70867728950e", true},
		{"0F8FAD5B-D9CB-469F-A165-70867728950E", "0f8fad5b-d9cb-469f-a165-70867728950e", true},
		{"{0f8fad5b-d9cb-469f-a165-70867728950e}", "", false},
		{"urn:uuid:0f8fad5b-d9cb-469f-a165-70867728950e", "", false},
		{"0f8fad5bd9cb469fa16570867728950e", "", false},
		{" 0f8fad5b-d9cb-469f-a165-70867728950e", "", false},
		{"0f8fad5b-d9cb-469f-a165-70867728950e\n", "", false},
		{"0f8fad5b-d9cb-469f-a165-70867728950g", "", false},
		{"run-42", "", false},
		{"", "", false},
	}
	for _, tt := range tests {
		got, ok := Parse(tt.in)
		if got != tt.want || ok != tt.wantOK {
			t.Errorf("Parse(%q) = %q, %v; want %q, %v", tt.in, got, ok, tt.want, tt.wantOK)
		}
	}
}

func TestNewIsCanonicalV4(t *testing.T) {
	id := New()
	if got, ok := Parse(id); !ok || got != id {
		t.Fatalf("New() = %q is not a canonical lowercase UUID", id)
	}
	if id[14] != '4' {
		t.Fatalf("New() = %q is not version 4", id)
	}
}

func TestTransport(t *testing.T) {
	var got []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = append(got, r.Header.Get(Header))
	}))
	defer srv.Close()
	client := &http.Client{Transport: &Transport{Base: http.DefaultTransport}}

	do := func(ctx context.Context) {
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL, nil)
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
	}

	id := New()
	do(WithState(context.Background(), &State{CorrelationID: id}))
	do(context.Background())

	if got[0] != id {
		t.Errorf("with state: header %q, want %q", got[0], id)
	}
	if got[1] != "" {
		t.Errorf("without state: header %q, want none", got[1])
	}
}
