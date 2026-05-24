# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Runtime deps: libpq for psycopg, iputils-ping only as a fallback diagnostic
# (icmplib uses raw sockets directly and doesn't need the binary).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app/

RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin app \
 && chown -R app:app /app

USER app

ENV DJANGO_SETTINGS_MODULE=fleetmanager.settings.prod

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]

# Default command is web; the watchdog service overrides it in compose.
CMD ["gunicorn", "fleetmanager.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
