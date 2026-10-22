.PHONY: sync lint test corpus-test integration-test smoke-test docker-build

UV ?= uv

sync:
	cd src && $(UV) sync

lint:
	cd src && $(UV) run ruff check . && $(UV) run ruff format --check .

test:
	cd src && $(UV) run pytest

# On demand: converts every run-001 PDF and, if tesseract is installed, every run003 scan (needs the tester/datasets symlink).
corpus-test:
	cd src && $(UV) run pytest -m corpus -v

# Needs RabbitMQ and MinIO: AMQP_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY.
integration-test:
	cd src && $(UV) run pytest -m integration

# Needs a running text-extractor too; queues and buckets must exist.
smoke-test:
	cd src && $(UV) run pytest -m smoke -v

docker-build:
	docker build -t invoices/text-extractor .
