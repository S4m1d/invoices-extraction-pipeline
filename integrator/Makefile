.PHONY: build run vet test integration-test smoke-test docker-build

build:
	cd src && go build -o ../bin/integrator ./cmd/integrator

run:
	cd src && go run ./cmd/integrator

vet:
	cd src && go vet ./... && go vet -tags integration,smoke ./...

test:
	cd src && go test ./...

# Needs RabbitMQ, MinIO (bucket "invoices") and PostgreSQL: AMQP_URL, RABBITMQ_MANAGEMENT_URL, MINIO_*,
# DATABASE_URL (integrator role) and POSTGRES_ADMIN_URL (superuser; creates a temporary database per test).
integration-test:
	cd src && go test -tags integration -count=1 ./internal/...

# Needs a running integrator, RabbitMQ, MinIO and PostgreSQL (DATABASE_URL); see src/smoke/smoke_test.go for the env vars.
smoke-test:
	cd src && go test -tags smoke -count=1 -v ./smoke/

docker-build:
	docker build -t invoices/integrator .
