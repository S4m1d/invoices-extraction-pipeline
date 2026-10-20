FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY src/pyproject.toml src/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/text_extractor ./text_extractor
RUN uv sync --frozen --no-dev

FROM python:3.13-slim
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD ["text-extractor", "healthcheck"]
ENTRYPOINT ["text-extractor"]
