# Infrastructure

All infrastructure runs in Docker containers. This file is the infra spec: what runs, how it is set up, and how to use it.

Contracts for queues and storage: see `/specs/pipeline-contracts.md`.

Status: spec approved; implementation follows.

## What runs

One Compose project, `infra/docker-compose.yml`:

| Service | Image | Role |
|---|---|---|
| `rabbitmq` | `rabbitmq:4.2-management` | Broker; management UI for inspection and tests |
| `minio` | MinIO, see MinIO image | Object storage |
| `postgres` | `postgres:17` | Database `invoices` |
| `fake-anthropic` | `python:3.13-slim` (fake-LLM mode only) | Fake Anthropic API from `fields-extractor/src/tests/fake_anthropic.py` |
| `init-rabbitmq` | `rabbitmq:4.2-management` | One-shot: imports the queues, exchange and policy, then exits |
| `init-minio` | `cgr.dev/chainguard/minio-client` (digest-pinned) | One-shot: creates the buckets, then exits |
| `ingestor` | built from `ingestor/` | |
| `text-extractor` | built from `text-extractor/` | |
| `fields-extractor` | built from `fields-extractor/` | |
| `integrator` | built from `integrator/` | |
| `tester` | built from `tester/` | Testing client: backend and frontend in one container (`tester/backend/specs/overview.md`) |

Service images are built from each module's `Dockerfile` (`build: ../<module>`) and tagged `invoices/<module>`. One replica each; more with `docker compose up --scale text-extractor=3` (the services support it: prefetch 1, shared spend counter, advisory-locked migrations).

## MinIO image

The official images can no longer be pulled anonymously (Docker Hub `minio/minio`, `quay.io/minio/minio`; checked 2026-10-16). Chainguard publishes current MinIO releases at `cgr.dev/chainguard/minio` (MinIO `RELEASE.2026-09-22T19-25-18Z` on 2026-10-16), free tier `latest` only. The image is pinned by digest (`cgr.dev/chainguard/minio@sha256:...`) so `up` is reproducible; updating MinIO means updating the digest. Same for `cgr.dev/chainguard/minio-client`. Decision record: `docs/decisions/0013-minio-image-from-chainguard.md`.

## Initialisation

All idempotent: running `up` again changes nothing.

- **RabbitMQ**: the user comes from `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS` (created on an empty data directory). The topology from the contract is in `infra/rabbitmq/definitions.json`: vhost `/`, queues `documents.ingested`, `documents.text_extracted`, `documents.fields_extracted`, `documents.failed` (durable, no arguments), exchange `dlx`, the three `.dlq` queues and their bindings, and policy `dead-letter`. One-shot `init-rabbitmq` imports it through the management API (`rabbitmqadmin definitions import`) once RabbitMQ is healthy; services start after it completed. Not loaded at boot (`load_definitions`): with boot-time definitions RabbitMQ skips creating the default user, so nobody could log in.
- **MinIO**: `init-minio` waits for MinIO, creates buckets `invoices`, `text-extractor-failures`, `fields-extractor-failures`, `fields-extractor-usage` (`mc mb --ignore-existing`) and exits. Services that use MinIO start after it completed.
- **PostgreSQL**: an init script in `infra/postgres/` (run by the image only on an empty data directory) creates roles `integrator` (login, owner of database `invoices`) and `tester` (login, read-only; grants come from the integrator's migration), with passwords from the environment. Schema: the integrator's migrations. Because the script runs only once, changing these passwords later needs `make reset` or a manual `ALTER ROLE`.

## Startup order and restarts

- `depends_on` with conditions: services wait for `rabbitmq` and `minio` to be healthy and for `init-rabbitmq` and `init-minio` to complete; `integrator` additionally waits for `postgres`.
- Health checks: the services' own (`/healthz` via their `healthcheck` subcommand); `rabbitmq-diagnostics check_port_connectivity`, MinIO `/minio/health/live` (the image has no curl or grep: an HTTP/1.1 request through bash's `/dev/tcp`), `pg_isready` for the infrastructure.
- `restart: unless-stopped` for all long-running containers. Services handle dependency outages themselves (reconnect, pause), so a restart is only for crashes.

## Configuration and secrets

- `infra/.env` (gitignored; `infra/.env.example` tracked) holds the infrastructure passwords: RabbitMQ user, MinIO root user, PostgreSQL superuser, `integrator` and `tester` passwords. Compose substitutes them into the services' environment.
- `ANTHROPIC_API_KEY` comes from `fields-extractor/.env` (`env_file:`), the same file the module's real-LLM tests use, so the key is kept in one place.
- `infra/tester-sources.yaml` (gitignored; `infra/tester-sources.example.yaml` tracked) lists the tester's data sources with host paths (`tester/backend/specs/data-sources.md`). It replaces `DATASETS_DIR`.
- Everything else uses the services' defaults (for example `LLM_DAILY_BUDGET_USD` 10); overrides go into `infra/.env`.

## Image versions

Infrastructure images use minor-version tags (`rabbitmq:4.2-management`, `postgres:17`), so pulls get patch releases. Chainguard images (MinIO, `minio-client`) are pinned by digest, since the free tier offers no version tags.

## LLM modes

- `make up`: `fields-extractor` calls the real Anthropic API (`claude-haiku-5-5`, about $0.0026 per invoice), limited by its daily budget (`LLM_DAILY_BUDGET_USD`, default $10).
- `make up-fake`: adds `infra/docker-compose.fake-llm.yml`, which starts `fake-anthropic` and points `fields-extractor` at it (`ANTHROPIC_BASE_URL`). The whole pipeline runs at no cost; every invoice gets the same canned extraction (the `inv_00000029` example). For plumbing tests only. A Compose profile alone is not enough, because the mode also changes `fields-extractor`'s environment.

## Ports and volumes

Published on `127.0.0.1` only (not reachable from other machines):

| Port | Service | Used by |
|---|---|---|
| 8080 | ingestor API | tester, manual uploads |
| 5672 | RabbitMQ AMQP | integration tests, smoke tests |
| 15672 | RabbitMQ management | inspection, integration tests (temporary vhosts) |
| 9000 | MinIO S3 API | tester (originals of failed documents), tests |
| 9001 | MinIO console | inspection |
| 5432 | PostgreSQL | tester (read-only role), tests |
| 8090 | tester UI and API | the person running tests |

Service health ports stay inside the Compose network.

Named volumes keep data across `down`/`up`: `rabbitmq-data`, `minio-data`, `postgres-data`, `tester-data` (run history).

`make reset` removes them.

Data sources: `make up` / `make up-fake` generate `infra/docker-compose.sources.yml` (gitignored) from `infra/tester-sources.yaml` with `tester sources-override`; it mounts each source read-only at `/sources/{name}` in the `tester` container and the YAML at `/config/sources.yaml`. A source path may be a symlink (Docker resolves the mount source). Details: `tester/backend/specs/data-sources.md`, Docker setup.

## Usage

`infra/Makefile`:

| Target | Does |
|---|---|
| `make up` | Build images, generate the data-source mounts, start everything (real LLM); waits until all are healthy |
| `make up-fake` | Same with the fake LLM (no API cost) |
| `make down` | Stop and remove containers (data kept) |
| `make reset` | `down` plus removing the volumes (all data lost) |
| `make ps`, `make logs` | Status, logs (`make logs s=integrator` for one service) |
| `make smoke` | Upload `inv_00000029` through the ingestor API, wait until its row is in PostgreSQL and compare key fields with its golden values. With the real LLM about $0.0004 per run; free in fake mode (the canned extraction is exactly this invoice's golden data) |

Notes:

- `make up` and `make up-fake` switch modes in place: each removes the other mode's containers (`--remove-orphans`), and only `fields-extractor` is recreated.
- Images are built without default build attestations (`BUILDX_NO_DEFAULT_ATTESTATIONS=1`), so an unchanged module builds to the same image ID and a repeated `up` recreates nothing.
- The `tester` consumes `documents.failed`. The modules' smoke tests also read failures from that queue, so run them with the tester stopped (`docker compose stop tester`).
- The modules' integration and smoke tests can run against this stack: use the credentials from `infra/.env` (for example `AMQP_URL=amqp://$RABBITMQ_USER:$RABBITMQ_PASSWORD@localhost:5672/`).

## Open questions

None.
