FROM python:3.11.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        pkg-config \
        default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt requirements/deployment.txt /tmp/requirements/
RUN pip install --no-cache-dir -r /tmp/requirements/deployment.txt \
    && useradd --create-home --uid 10001 web \
    && mkdir -p /app/backend /app/staticfiles \
    && chown -R web:web /app

COPY --chown=web:web backend/ /app/backend/

WORKDIR /app/backend
USER web
EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]
