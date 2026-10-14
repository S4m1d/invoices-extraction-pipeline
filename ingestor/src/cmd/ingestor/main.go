// Command ingestor runs the ingestor HTTP service.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/api"
	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/config"
	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/queue"
	"github.com/S4m1d/invoices-extraction-pipeline/ingestor/internal/storage"
)

const (
	readTimeout     = 30 * time.Second // R7
	writeTimeout    = 60 * time.Second
	idleTimeout     = 60 * time.Second
	shutdownTimeout = 25 * time.Second
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck())
	}
	if err := run(); err != nil {
		slog.Error("ingestor stopped", "error", err.Error())
		os.Exit(1)
	}
}

// healthcheck probes /healthz on the local server. It exists because the
// runtime image has no shell or curl for a Docker HEALTHCHECK.
func healthcheck() int {
	addr := os.Getenv("INGESTOR_HTTP_ADDR")
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

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: cfg.LogLevel}))
	slog.SetDefault(log)

	store, err := storage.NewMinio(cfg.MinioEndpoint, cfg.MinioAccessKey, cfg.MinioSecretKey, cfg.MinioUseSSL)
	if err != nil {
		return err
	}
	pub := queue.NewPublisher(cfg.AMQPURL, api.QueueName)
	defer pub.Close()

	srv := &http.Server{
		Addr:         cfg.HTTPAddr,
		Handler:      api.NewServer(store, pub, log, cfg.MaxUploads).Handler(),
		ReadTimeout:  readTimeout,
		WriteTimeout: writeTimeout,
		IdleTimeout:  idleTimeout,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	errc := make(chan error, 1)
	go func() {
		log.Info("ingestor listening", "addr", cfg.HTTPAddr)
		errc <- srv.ListenAndServe()
	}()

	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
	}

	log.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
