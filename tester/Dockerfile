# Tester: backend and frontend in one image (tester/backend/specs/overview.md).

FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/src/package.json frontend/src/package-lock.json ./
RUN npm ci
COPY frontend/src/ ./
RUN npm run build

FROM rust:1.98-bookworm AS backend
# DuckDB's bundled C++ sources need about 1.5 GB per compiler process; keep it at one on small hosts.
ARG BUILD_JOBS=1
ENV CARGO_BUILD_JOBS=${BUILD_JOBS} NUM_JOBS=${BUILD_JOBS}
WORKDIR /src
COPY backend/src/ ./
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked && cp target/release/tester /tester && mkdir /data

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=backend /tester /tester
COPY --from=frontend /src/dist /app/static
# Run history (SQLite) lives on the tester-data volume; the image's /data gives it nonroot ownership.
COPY --from=backend --chown=65532:65532 /data /data
EXPOSE 8090
ENTRYPOINT ["/tester"]
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD ["/tester", "healthcheck"]
