ARG APP_VERSION=development

# Build the React frontend.
FROM --platform=$BUILDPLATFORM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ARG APP_VERSION
ARG VITE_BASEMAP_TILEJSON_URL=https://tiles.openfreemap.org/planet
ENV VITE_API_BASE_URL=
ENV VITE_APP_VERSION=$APP_VERSION
ENV VITE_BASEMAP_TILEJSON_URL=$VITE_BASEMAP_TILEJSON_URL
RUN npm run build

# Run FastAPI and serve the compiled frontend from the same container.
FROM python:3.11.17-slim-trixie@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377
WORKDIR /app
ARG APP_VERSION
LABEL org.opencontainers.image.source="https://github.com/johnngone/nyc-trash-map"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.version=$APP_VERSION
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY backend/app ./backend/app
# Explicit runtime allowlist: tests, fixture builders, and research tools stay in CI.
COPY scripts/activate_release.py scripts/archive_security.py scripts/build_pilot.py \
    scripts/build_tiles.py scripts/container_entrypoint.sh scripts/load_processed.py \
    scripts/promote_staging.py scripts/recovery_shadow.py scripts/release_validation.py \
    scripts/run_refresh.py scripts/run_scheduler.py ./scripts/
COPY --from=frontend-build /app/frontend/dist ./frontend-dist
# Installation tools and their vendored libraries are removed after installation.
RUN --mount=type=bind,source=requirements.txt,target=/tmp/requirements.txt \
    apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --upgrade pip==26.2.2 setuptools==84.1.0 wheel==0.46.3 \
    && python -m pip install --no-cache-dir --no-compile --require-hashes -r /tmp/requirements.txt \
    && python -m pip uninstall -y pip setuptools wheel \
    && rm -rf /usr/local/lib/python3.11/ensurepip \
    && chmod +x /app/scripts/container_entrypoint.sh \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /app/data app \
    && mkdir -p /app/data \
    && chown app:app /app/data

ENV DATA_MANIFEST_PATH=/app/data/data_manifest.json
ENV DATA_REFRESH_ENABLED=true
ENV DATA_REFRESH_INTERVAL_DAYS=14
ENV DATA_REFRESH_ON_STARTUP=true
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
USER 10001:10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/live')"]
CMD ["/app/scripts/container_entrypoint.sh"]
