# AcumenAI dashboard JSON API — Cloud Run container.
# Built from the repo root:  gcloud run deploy --source .  (Cloud Build auto-detects
# this root Dockerfile). Serves dashboard/app.py only; the Cloud Functions entry
# (main.py) is deployed separately and is unaffected by this file.
FROM python:3.14-slim

# System libs: pyodbc needs unixODBC; build-essential covers any sdist without a wheel.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential unixodbc-dev \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# App source. .gcloudignore trims the build context (no .venv/data/archive/.git).
COPY . .

# Cloud Run sets $PORT (default 8080); gunicorn must bind 0.0.0.0:$PORT.
# Single worker keeps the in-process BigQuery client + demo cache simple; raise
# --workers when you need more concurrency (each worker re-bakes its own demo).
ENV PORT=8080
CMD exec gunicorn -k uvicorn.workers.UvicornWorker dashboard.app:app \
    --bind "0.0.0.0:${PORT}" --workers 1 --threads 8 --timeout 120
