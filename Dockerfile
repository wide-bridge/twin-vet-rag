# TwinVet Care — one image: FastAPI API + built React SPA on the same origin (docs/05: no separate web server).
# Build:  docker build -t twinvet-care .
# Run:    docker compose --profile app up -d     (see docker-compose.yml; secrets come from the environment)

FROM node:20.19-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
# runtime only: test/dev tools (pytest, playwright) are not needed in the image
RUN grep -vE '^(pytest|playwright)' requirements.txt > /tmp/req.txt && pip install --no-cache-dir -r /tmp/req.txt
COPY backend/ backend/
COPY migrations/ migrations/
COPY alembic.ini ./
COPY scripts/__init__.py scripts/cleanup_sessions.py scripts/
COPY --from=web /web/dist frontend/dist
RUN useradd --create-home --uid 10001 app
USER app
EXPOSE 8000
# migrations run on start; one API process (the answer worker runs inside it, docs/04 §3)
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
