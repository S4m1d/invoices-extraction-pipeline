// Package health serves GET /healthz (integrator/specs/overview.md, Health).
package health

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"slices"
	"sync"
	"time"
)

type Check struct {
	Name  string
	Check func(ctx context.Context) error
}

// Handler runs the checks concurrently (3 s timeout) and logs only when the failing set changes.
func Handler(checks []Check, log *slog.Logger) http.Handler {
	var mu sync.Mutex
	var lastFailing []string
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/healthz" {
			http.NotFound(w, r)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		errs := make([]error, len(checks))
		var wg sync.WaitGroup
		for i, c := range checks {
			wg.Add(1)
			go func() {
				defer wg.Done()
				errs[i] = c.Check(ctx)
			}()
		}
		wg.Wait()
		var failing []string
		details := map[string]string{}
		for i, c := range checks {
			if errs[i] != nil {
				failing = append(failing, c.Name)
				details[c.Name] = errs[i].Error()
			}
		}
		mu.Lock()
		if !slices.Equal(failing, lastFailing) {
			if len(failing) > 0 {
				log.Warn("health check failed", "failing", failing, "errors", details)
			} else {
				log.Info("health check ok again")
			}
			lastFailing = failing
		}
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if len(failing) > 0 {
			w.WriteHeader(http.StatusServiceUnavailable)
			// Shape: /specs/pipeline-contracts.md, Health endpoint.
			_ = json.NewEncoder(w).Encode(map[string]any{"status": "unavailable", "failing": failing, "errors": details})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})
}
