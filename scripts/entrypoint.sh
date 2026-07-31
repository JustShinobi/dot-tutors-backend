#!/bin/sh
# Container entrypoint: bring the schema up to date, then serve.
#
# Running migrations here is a deliberate trade-off for a single-instance deployment. With
# several replicas they would race, and the correct shape is a one-shot job in the pipeline —
# which is why this is a flag rather than a hard-coded step. `alembic upgrade head` is
# idempotent, so a restart costs nothing when there is nothing to apply.

set -eu

if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
    echo "==> alembic upgrade head"
    alembic upgrade head
fi

if [ "${RUN_SEED_ON_START:-false}" = "true" ]; then
    # Idempotent: it will not duplicate the administrator, the tutors or the embed key.
    echo "==> seed"
    python -m scripts.seed
fi

echo "==> uvicorn"
exec "$@"
