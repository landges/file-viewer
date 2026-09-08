# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.13-slim-bookworm AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CACHE_DIR=/var/cache/file-viewer \
    WORK_DIR=/tmp/file-viewer
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress \
    p7zip-full unar \
    fonts-dejavu-core fonts-liberation fontconfig tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

FROM python-base AS development
ENV PYTHONPATH=/app
COPY backend/app/ ./app/
COPY scripts/ ./scripts/
RUN mkdir -p /var/cache/file-viewer /tmp/file-viewer
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]

FROM development AS test
COPY backend/requirements-dev.txt ./requirements-dev.txt
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY backend/tests/ ./tests/
CMD ["pytest", "-q", "tests"]

FROM python-base AS runtime
ENV PYTHONPATH=/app
COPY backend/app/ ./app/
COPY scripts/ ./scripts/
COPY --from=frontend-build /build/dist ./static/
RUN mkdir -p /var/cache/file-viewer /tmp/file-viewer && \
    useradd --system --uid 10001 --home /app viewer && \
    chown -R viewer:viewer /app /var/cache/file-viewer /tmp/file-viewer
USER viewer
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
