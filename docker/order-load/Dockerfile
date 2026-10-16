FROM python:3.11.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/backend
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential pkg-config default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements/base.txt requirements/deployment.txt requirements/load.txt /tmp/requirements/
RUN pip install --no-cache-dir -r /tmp/requirements/deployment.txt -r /tmp/requirements/load.txt \
    && useradd --create-home --uid 10001 loadtest && mkdir /artifacts \
    && chown loadtest:loadtest /artifacts
COPY backend/ /app/backend/
COPY loadtests/ /app/loadtests/
WORKDIR /app
USER loadtest
CMD ["gunicorn", "loadtests.order_list.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "30", "--error-logfile", "-"]
