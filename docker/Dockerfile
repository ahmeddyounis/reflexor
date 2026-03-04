FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE alembic.ini /app/
COPY src /app/src
COPY alembic /app/alembic
COPY scripts /app/scripts
COPY docker /app/docker

RUN python -m pip install -U pip \
    && python -m pip install --no-cache-dir ".[postgres,redis]"
