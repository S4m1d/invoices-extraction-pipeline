# 0013. MinIO image from Chainguard, pinned by digest

- Status: accepted
- Date: 2026-10-16

## Context

ADR 0002 chose MinIO for object storage. The official MinIO images can no longer be pulled anonymously: `minio/minio` on Docker Hub and `quay.io/minio/minio` both refuse (checked 2026-10-14 and 2026-10-16). During module development MinIO was compiled from source in a `golang` container, which took about 10 minutes on a cold cache. Options considered:

- Build from source in `infra/` (pinned release; about 10 minutes on every new machine).
- `bitnamilegacy/minio` (frozen at a 2025-05 development build, no updates).
- Another S3-compatible store with public images (SeaweedFS, Garage): would supersede ADR 0002 and needs compatibility testing with the MinIO clients the services use.
- `cgr.dev/chainguard/minio`: current official MinIO releases built by Chainguard (`RELEASE.2026-09-22T19-25-18Z` on 2026-10-16); the free tier publishes only the `latest` tag.

## Decision

Use `cgr.dev/chainguard/minio` and `cgr.dev/chainguard/minio-client`, pinned by `sha256` digest in `infra/docker-compose.yml`. MinIO itself stays the object store (ADR 0002 unchanged).

## Consequences

- `make up` pulls MinIO in seconds; no compilation.
- We depend on a third party (Chainguard) to build MinIO. Pinning by digest means an image is never replaced silently.
- Updating MinIO is a deliberate change: pull `latest`, check the version, update the digest.
- If Chainguard stops publishing the image, the fallbacks are building from source or moving to another S3-compatible store (a new ADR).
