FROM python:3.11.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        pkg-config \
        default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt /tmp/requirements/base.txt
RUN pip install --no-cache-dir -r /tmp/requirements/base.txt \
    && useradd --create-home --uid 10001 payment-worker \
    && mkdir -p /app/backend

WORKDIR /app/backend
USER payment-worker

# Compose supplies the read-only backend mount and environment-specific queue.
CMD ["celery", "-A", "config", "worker", "--pool=prefork", "--concurrency=1"]
