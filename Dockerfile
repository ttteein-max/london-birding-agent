FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    BIODIVERSITY_PUBLIC_DEMO=true \
    BIODIVERSITY_ALLOWED_RUN_MODES=fixture/scripted \
    BIODIVERSITY_SERVE_FRONTEND=true \
    BIODIVERSITY_EXPOSE_API_DOCS=false \
    BIODIVERSITY_CHECKPOINT_DB=/var/lib/biodiversity/checkpoints.sqlite \
    BIODIVERSITY_RUN_CATALOG_DB=/var/lib/biodiversity/catalog.sqlite \
    BIODIVERSITY_REPORT_ROOT=/var/lib/biodiversity/reports \
    BIODIVERSITY_FRONTEND_DIST=/app/frontend/dist

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY data/boundaries/ ./data/boundaries/
COPY data/fixtures/ ./data/fixtures/
COPY data/osm/ ./data/osm/
COPY --from=frontend-build /build/frontend/dist/ ./frontend/dist/

RUN useradd --create-home --uid 10001 biodiversity \
    && mkdir -p /var/lib/biodiversity/reports \
    && chown -R biodiversity:biodiversity /var/lib/biodiversity

USER biodiversity

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT', '8000') + '/api/v1/health', timeout=4)"

CMD ["sh", "-c", "python -m uvicorn app.biodiversity.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
