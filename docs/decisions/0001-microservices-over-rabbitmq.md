# 0001. Pipeline as microservices connected via RabbitMQ

- Status: accepted
- Date: 2026-10-13

## Context

The pipeline has distinct steps (ingestion, layout/text extraction, fields extraction, integration) with different stacks (Go, Python) and later additions (scan processing, other target tables).

## Decision

Each step is a separate module/microservice. Services are connected with RabbitMQ queues. All infrastructure runs in Docker containers.

## Consequences

- Steps can use different languages and be scaled and replaced independently.
- Queue names, message envelope and failure handling must be specified centrally (`specs/pipeline-contracts.md`).
- Local setup requires Docker Compose.
