// Package config reads the integrator configuration from the environment
// (integrator/specs/overview.md, Health, logging, configuration).
package config

import (
	"fmt"
	"log/slog"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	AMQPURL         string
	MinioEndpoint   string
	MinioAccessKey  string
	MinioSecretKey  string
	MinioUseSSL     bool
	DatabaseURL     string
	HTTPAddr        string
	DBCheckInterval time.Duration
	LogLevel        slog.Level
}

// Load reads the configuration and reports all missing or invalid variables at once.
func Load(getenv func(string) string) (Config, error) {
	if getenv == nil {
		getenv = os.Getenv
	}
	var missing, invalid []string
	required := func(name string) string {
		v := getenv(name)
		if v == "" {
			missing = append(missing, name)
		}
		return v
	}
	optional := func(name, def string) string {
		if v := getenv(name); v != "" {
			return v
		}
		return def
	}
	c := Config{
		AMQPURL:        required("AMQP_URL"),
		MinioEndpoint:  required("MINIO_ENDPOINT"),
		MinioAccessKey: required("MINIO_ACCESS_KEY"),
		MinioSecretKey: required("MINIO_SECRET_KEY"),
		DatabaseURL:    required("DATABASE_URL"),
		HTTPAddr:       optional("INTEGRATOR_HTTP_ADDR", ":8080"),
	}
	ssl, err := strconv.ParseBool(optional("MINIO_USE_SSL", "false"))
	if err != nil {
		invalid = append(invalid, "MINIO_USE_SSL")
	}
	c.MinioUseSSL = ssl
	secs, err := strconv.ParseFloat(optional("INTEGRATOR_DB_CHECK_INTERVAL", "10"), 64)
	if err != nil || secs <= 0 {
		invalid = append(invalid, "INTEGRATOR_DB_CHECK_INTERVAL")
	}
	c.DBCheckInterval = time.Duration(secs * float64(time.Second))
	if err := c.LogLevel.UnmarshalText([]byte(optional("LOG_LEVEL", "info"))); err != nil {
		invalid = append(invalid, "LOG_LEVEL")
	}
	if len(missing) > 0 || len(invalid) > 0 {
		return Config{}, fmt.Errorf("config: missing [%s], invalid [%s]", strings.Join(missing, ", "), strings.Join(invalid, ", "))
	}
	return c, nil
}
