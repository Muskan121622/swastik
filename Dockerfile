# syntax=docker/dockerfile:1
#
# Single-service image: one container serves the React build AND the API,
# so the deployment has one public origin (no CORS, one URL to review).
#
#   docker build -t swasthiq .
#   docker run -p 8005:8005 -e PORT=8005 -e GROQ_API_KEY=... swasthiq

# ---------------- stage 1: front end ----------------
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build                      # tsc typecheck + vite build -> /fe/dist

# ---------------- stage 2: runtime ----------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /srv/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /fe/dist /srv/frontend/dist

# Render injects PORT; 8000 is the fallback it expects locally.
EXPOSE 8000

# Deliberately ONE process: the sqlite "one ACTIVE booking per slot"
# invariant and the WAL busy-timeout are per-connection, so multiple
# workers would only add lock contention, not throughput.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
