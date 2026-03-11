FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

COPY pyproject.toml README.md LICENSE alembic.ini /build/
COPY src /build/src
COPY alembic /build/alembic
COPY scripts /build/scripts
COPY docker /build/docker

RUN python -m pip install -U pip \
    && python -m pip install --no-cache-dir "/build[postgres,redis]"

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 10001 reflexor \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/reflexor reflexor

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY scripts /app/scripts
COPY docker /app/docker

RUN mkdir -p /workspace /tmp \
    && chown -R reflexor:reflexor /app /workspace /tmp /home/reflexor

USER reflexor

EXPOSE 8000

CMD ["reflexor", "run", "api", "--host", "0.0.0.0", "--port", "8000", "--no-reload"]
