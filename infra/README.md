# Infrastructure

All infrastructure runs in Docker containers. Status: skeleton, no files yet.

Planned:
- `docker-compose.yml` with RabbitMQ, MinIO, PostgreSQL and the services.
- Broker and bucket initialisation (queues, bucket).

Contracts for queues and storage: see `/specs/pipeline-contracts.md`.

To set up (from the contract):
- Queues `documents.ingested`, `documents.text_extracted`, `documents.fields_extracted`, `documents.failed`, durable, no arguments.
- Dead-lettering: exchange `dlx`, `<queue>.dlq` queues bound to it, and the `dead-letter` policy (contract, Dead-lettering).
- Buckets `invoices` and `text-extractor-failures` (contract, Object storage layout).
- MinIO image: official images can no longer be pulled anonymously from Docker Hub or quay.io; decide how to obtain one.
