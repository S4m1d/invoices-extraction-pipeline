# 0016. Tester: no per-document timeout; Stop cancels documents in the pipeline

- Status: accepted
- Date: 2026-10-23

## Context

The tester marked a document `timeout` when neither its row nor a failure arrived within `TESTER_DOCUMENT_TIMEOUT` (10 min) of active pipeline time after its upload. `parallelism` limits only the uploads, and the ingestor accepts an upload within seconds, so a whole dataset is uploaded early in the run while the pipeline works through its queues document by document. On a 200-document run only about 50 documents finished within 10 minutes of their upload; the rest were marked `timeout` while still queued and finished later unseen. The timeout measured queue length, not a stuck document.

The pipeline already reports every failure it detects on `documents.failed` (ADR 0009, 0011), and waits for limits and outages are visible through the services' `/healthz`.

## Decision

- No per-document timeout. A document stays `uploaded` until its row or failure arrives; tracking (PostgreSQL polling, `documents.failed`) is unchanged. `TESTER_DOCUMENT_TIMEOUT` is removed.
- Stop ends tracking at once: documents still in the pipeline become `cancelled`, the run ends as `stopped` and gets its summary. This is the way out when a document never finishes.
- Old runs keep their `timeout` documents and timeout reasons; the columns stay.

Rejected: limiting documents in the pipeline (not only uploads) to `parallelism`, which would keep the timeout meaningful. It slows runs to the tester's pace and changes what `parallelism` means; it can be added later if per-document durations need to exclude queue time.

## Consequences

- A document lost by the pipeline keeps its run open until the run is stopped.
- Per-document durations include queue time behind the run's other documents.
- Results that arrive after Stop for cancelled documents are ignored by the tester.
