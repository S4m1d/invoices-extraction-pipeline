// Package config reads the ingestor configuration from the environment
// (see ingestor/specs/overview.md, Configuration).
package config

import (
	"fmt"
	"log/slog"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	HTTPAddr       string
	AMQPURL        string
	MinioEndpoint  string
	MinioAccessKey string
	MinioSecretKey string
	MinioUseSSL    bool
	LogLevel       slog.Level
}

// Load reads the configuration and reports all missing or invalid variables at once.
func Load() (Config, error) {
	var missing, invalid []string
	required := func(name string) string {
		v := os.Getenv(name)
		if v == "" {
			missing = append(missing, name)
		}
		return v
	}
	optional := func(name, def string) string {
		if v := os.Getenv(name); v != "" {
			return v
		}
		return def
	}

	c := Config{
		HTTPAddr:       optional("INGESTOR_HTTP_ADDR", ":8080"),
		AMQPURL:        required("AMQP_URL"),
		MinioEndpoint:  required("MINIO_ENDPOINT"),
		MinioAccessKey: required("MINIO_ACCESS_KEY"),
		MinioSecretKey: required("MINIO_SECRET_KEY"),
	}

	ssl, err := strconv.ParseBool(optional("MINIO_USE_SSL", "false"))
	if err != nil {
		invalid = append(invalid, "MINIO_USE_SSL")
	}
	c.MinioUseSSL = ssl

	if err := c.LogLevel.UnmarshalText([]byte(optional("LOG_LEVEL", "info"))); err != nil {
		invalid = append(invalid, "LOG_LEVEL")
	}

	if len(missing) > 0 || len(invalid) > 0 {
		return Config{}, fmt.Errorf("config: missing [%s], invalid [%s]",
			strings.Join(missing, ", "), strings.Join(invalid, ", "))
	}
	return c, nil
}
