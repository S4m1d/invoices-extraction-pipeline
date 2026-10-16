// Command integrator writes extracted invoices to PostgreSQL (integrator/specs).
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/config"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/db"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/health"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/processor"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/storage"
	"github.com/S4m1d/invoices-extraction-pipeline/integrator/internal/worker"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck())
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load(nil)
	if err != nil {
		return err
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))
	slog.SetDefault(log)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	store, err := db.Open(ctx, cfg.DatabaseURL)
	if err != nil {
		return err
	}
	defer store.Close()
	migrateCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	err = store.Migrate(migrateCtx, log)
	cancel()
	if err != nil {
		return fmt.Errorf("cannot start: %w", err) // spec: failed migration = service does not start
	}

	objects, err := storage.NewMinio(cfg.MinioEndpoint, cfg.MinioAccessKey, cfg.MinioSecretKey, cfg.MinioUseSSL)
	if err != nil {
		return err
	}
	w := &worker.Worker{
		URL:           cfg.AMQPURL,
		DBCheck:       store.Ping,
		CheckInterval: cfg.DBCheckInterval,
		Log:           log,
	}
	w.Handler = processor.New(objects, store, w, log)

	srv := &http.Server{
		Addr: cfg.HTTPAddr,
		Handler: health.Handler([]health.Check{
			{Name: "rabbitmq", Check: func(context.Context) error {
				if !w.Connected.Load() && !w.Paused.Load() {
					return errors.New("not connected to rabbitmq")
				}
				return nil
			}},
			{Name: "minio", Check: objects.Check},
			{Name: "postgres", Check: store.Ping},
		}, log),
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("health server stopped", "error", err.Error())
		}
	}()

	log.Info("integrator started", "health_addr", cfg.HTTPAddr)
	w.Run(ctx)
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
	log.Info("stopped")
	return nil
}

// healthcheck probes /healthz; used by the Docker HEALTHCHECK (the runtime image has no curl).
func healthcheck() int {
	addr := os.Getenv("INTEGRATOR_HTTP_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	if strings.HasPrefix(addr, ":") {
		addr = "localhost" + addr
	}
	client := http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get("http://" + addr + "/healthz")
	if err != nil {
		return 1
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}
