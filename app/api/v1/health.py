"""Liveness and readiness probes.

The two answer different questions, and conflating them is how a deployment ends up in a
restart loop: liveness asks "is this process wedged?", readiness asks "can it serve traffic
right now?". A database that is briefly unreachable should take an instance *out of rotation*,
not kill it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["health"])

SERVICE = "dot-tutors-backend"
VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    service: str
    version: str
    database: str
    agent: str


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
async def healthz() -> HealthResponse:
    """Answers as long as the process is running. Does not touch the database."""
    return HealthResponse(status="ok", service=SERVICE, version=VERSION)


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Verifica as dependencias necessarias para atender: banco e disponibilidade do agente. "
        "Responde 503 quando o banco esta inacessivel."
    ),
    responses={503: {"description": "Alguma dependencia essencial esta indisponivel"}},
)
async def readyz(session: SessionDep, request: Request, response: Response) -> ReadinessResponse:
    try:
        await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        logger.exception("readiness_database_unavailable")
        database = "unavailable"

    # An unconfigured agent degrades the product (no chat) but leaves the admin API working, so
    # it is *reported* without failing the probe — an instance serving only the panel is still
    # worth routing to, and a probe that fails on it would take the panel down too.
    agent = "ok" if getattr(request.app.state, "agent_runner", None) is not None else "unavailable"

    if database != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="degraded",
            service=SERVICE,
            version=VERSION,
            database=database,
            agent=agent,
        )

    return ReadinessResponse(
        status="ok", service=SERVICE, version=VERSION, database=database, agent=agent
    )
