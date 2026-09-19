# syntax=docker/dockerfile:1

FROM python:3.13-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv

ENV UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE .python-version ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 signal \
    && mkdir /data \
    && chown signal:signal /data

WORKDIR /app
COPY --from=builder --chown=signal:signal /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SIGNAL_DATA_DIR=/data \
    SIGNAL_HOST=0.0.0.0

LABEL org.opencontainers.image.source="https://github.com/lfnovo/signal" \
      org.opencontainers.image.description="A personal reading inbox with MCP access" \
      org.opencontainers.image.licenses="MIT"

VOLUME ["/data"]
EXPOSE 8020
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/api/health', timeout=4)"]

USER signal
ENTRYPOINT ["signal"]
CMD ["serve"]
