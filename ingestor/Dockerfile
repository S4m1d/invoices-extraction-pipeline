FROM golang:1.26-alpine AS build
WORKDIR /src
COPY src/go.mod src/go.sum ./
RUN go mod download
COPY src/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /ingestor ./cmd/ingestor

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /ingestor /ingestor
EXPOSE 8080
ENTRYPOINT ["/ingestor"]
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD ["/ingestor", "healthcheck"]
