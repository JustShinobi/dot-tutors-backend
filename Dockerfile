# syntax=docker/dockerfile:1

# Two stages so the runtime image carries the installed dependencies but not the compilers that
# built them: `bcrypt`, `asyncpg` and `selectolax` all ship C extensions.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# The dependency metadata alone first: this layer is rebuilt only when the manifest changes, so
# editing application code does not reinstall the world.
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Never root: a container escape should not start from uid 0.
RUN useradd --create-home --uid 1000 tutors

# The working directory has to belong to that user, not just the files inside it. `WORKDIR`
# creates it as root, and `COPY --chown` only touches what it copies — so with the default
# SQLite URL the application could not create its database file and died on the first
# migration. Postgres hides the problem; whoever runs the image as shipped hits it.
RUN mkdir -p /app && chown tutors:tutors /app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=tutors:tutors app ./app
COPY --chown=tutors:tutors alembic ./alembic
COPY --chown=tutors:tutors scripts ./scripts
COPY --chown=tutors:tutors alembic.ini pyproject.toml ./

RUN chmod +x scripts/entrypoint.sh

USER tutors

EXPOSE 8000

ENTRYPOINT ["./scripts/entrypoint.sh"]

# `--proxy-headers` makes uvicorn read the scheme and host a reverse proxy forwarded, so
# generated URLs are https rather than http. It does *not* set the client IP for our rate
# limiting — that is `TRUSTED_PROXY_HOPS`, which we apply ourselves and can reason about.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
