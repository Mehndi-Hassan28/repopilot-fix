FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api /app/apps/api

RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir "/app/apps/api"

FROM python:3.12-slim AS runner

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 1000 -m -s /bin/bash repopilot

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY apps/api /app/apps/api

ENV PYTHONPATH=/app/apps/api/src
ENV PYTHONUNBUFFERED=1

USER repopilot

EXPOSE 8000

CMD ["uvicorn", "repopilot_api.main:app", "--app-dir", "/app/apps/api/src", "--host", "0.0.0.0", "--port", "8000"]
