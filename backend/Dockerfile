# Stockora API for the free online demo (Render). Build context: this backend folder.
FROM python:3.12-slim

# libgomp1: OpenMP runtime that XGBoost needs. tzdata: so "today" is India's date, not UTC.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata \
    STOCKORA_DB=/app/data/stockora.db \
    STOCKORA_MODEL_DIR=/app/data/models \
    STOCKORA_AUTO_RETRAIN=0

WORKDIR /app
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY stockora ./stockora

# Build the demo shop and train its forecasts now, on the build machine, and save them in the image.
# The free server has a tenth of a CPU: training there would take minutes, loading takes seconds.
# Every restart starts again from this saved copy, so visitors' changes are reset.
RUN mkdir -p /app/data && python -c "from stockora.api.main import state; s = state(); s.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')"

EXPOSE 8000
CMD ["sh", "-c", "uvicorn stockora.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
