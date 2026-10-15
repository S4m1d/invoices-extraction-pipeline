.PHONY: sync lint test llm-test integration-test smoke-test docker-build

UV ?= uv

sync:
	cd src && $(UV) sync

lint:
	cd src && $(UV) run ruff check . && $(UV) run ruff format --check .

test:
	cd src && $(UV) run pytest

# Calls the real LLM API (costs money): ANTHROPIC_API_KEY.
llm-test:
	cd src && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(UV) run pytest -m llm -v -s

# Needs RabbitMQ and MinIO: AMQP_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY.
integration-test:
	cd src && $(UV) run pytest -m integration

# Needs a running fields-extractor too (calls the LLM API); queues and buckets must exist.
smoke-test:
	cd src && $(UV) run pytest -m smoke -v

docker-build:
	docker build -t invoices/fields-extractor .
