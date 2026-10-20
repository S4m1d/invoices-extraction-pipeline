.PHONY: build run vet test integration-test smoke-test docker-build

build:
	cd src && go build -o ../bin/ingestor ./cmd/ingestor

run:
	cd src && go run ./cmd/ingestor

vet:
	cd src && go vet ./... && go vet -tags integration,smoke ./...

test:
	cd src && go test ./...

# Needs RabbitMQ and MinIO (bucket "invoices"): AMQP_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY.
integration-test:
	cd src && go test -tags integration -count=1 ./internal/...

# Needs a running ingestor, RabbitMQ and MinIO; see src/smoke/smoke_test.go for the env vars.
smoke-test:
	cd src && go test -tags smoke -count=1 -v ./smoke/

docker-build:
	docker build -t invoices/ingestor .
