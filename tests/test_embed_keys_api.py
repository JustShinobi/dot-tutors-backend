"""Embed key management and runtime authorisation (PRD 3.2 and 3.4)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.core.errors import (
    EmbedKeyNotFoundError,
    EmbedKeyRevokedError,
    OriginNotAllowedError,
    TutorInactiveError,
)
from app.repositories.embed import EmbedKeyRepository
from app.services.embed_service import PUBLIC_KEY_PREFIX, EmbedService
from app.services.tutor_service import TutorService

Headers = dict[str, str]


async def _create_tutor(client: AsyncClient, headers: Headers, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Tutor de Onboarding",
        "system_instructions": "Voce e um tutor. Use as fontes configuradas.",
    }
    payload.update(overrides)
    response = await client.post("/api/v1/tutors", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _create_key(
    client: AsyncClient, headers: Headers, tutor_id: str, **payload: Any
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/tutors/{tutor_id}/embed-keys", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- management ------------------------------------------------------------


async def test_create_key_returns_a_public_key(client: AsyncClient, auth_headers: Headers) -> None:
    tutor = await _create_tutor(client, auth_headers)

    key = await _create_key(
        client,
        auth_headers,
        tutor["id"],
        label="Site institucional",
        allowed_origins=["https://cliente.com"],
    )

    assert key["public_key"].startswith(PUBLIC_KEY_PREFIX)
    assert key["is_active"] is True
    assert key["allowed_origins"] == ["https://cliente.com"]
    assert key["revoked_at"] is None


async def test_two_keys_never_collide(client: AsyncClient, auth_headers: Headers) -> None:
    tutor = await _create_tutor(client, auth_headers)

    first = await _create_key(client, auth_headers, tutor["id"])
    second = await _create_key(client, auth_headers, tutor["id"])

    assert first["public_key"] != second["public_key"]


async def test_origins_are_normalized_on_write(client: AsyncClient, auth_headers: Headers) -> None:
    tutor = await _create_tutor(client, auth_headers)

    key = await _create_key(
        client,
        auth_headers,
        tutor["id"],
        allowed_origins=["https://CLIENTE.com:443/", "https://cliente.com"],
    )

    assert key["allowed_origins"] == ["https://cliente.com"]


async def test_an_invalid_origin_is_rejected_at_creation(
    client: AsyncClient, auth_headers: Headers
) -> None:
    tutor = await _create_tutor(client, auth_headers)

    response = await client.post(
        f"/api/v1/tutors/{tutor['id']}/embed-keys",
        json={"allowed_origins": ["cliente.com/app"]},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_key_creation_falls_back_to_the_configured_default_origins(
    client: AsyncClient, auth_headers: Headers
) -> None:
    tutor = await _create_tutor(client, auth_headers)

    key = await _create_key(client, auth_headers, tutor["id"])

    assert key["allowed_origins"] == ["http://localhost:3000"]


async def test_revoke_deactivates_the_key(client: AsyncClient, auth_headers: Headers) -> None:
    tutor = await _create_tutor(client, auth_headers)
    key = await _create_key(client, auth_headers, tutor["id"])

    revoked = await client.post(f"/api/v1/embed-keys/{key['id']}/revoke", headers=auth_headers)

    assert revoked.status_code == 200
    assert revoked.json()["is_active"] is False
    assert revoked.json()["revoked_at"] is not None


async def test_revoking_an_unknown_key_returns_404(
    client: AsyncClient, auth_headers: Headers
) -> None:
    response = await client.post("/api/v1/embed-keys/nao-existe/revoke", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EMBED_KEY_NOT_FOUND"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/tutors/x/embed-keys"),
        ("get", "/api/v1/tutors/x/embed-keys"),
        ("post", "/api/v1/embed-keys/x/revoke"),
        ("get", "/api/v1/tutors/x/embed-snippet?key_id=y"),
    ],
)
async def test_embed_key_routes_require_an_admin_token(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method.upper(), path, json={})

    assert response.status_code == 401


# --- snippet ---------------------------------------------------------------


async def test_snippet_contains_a_ready_to_paste_iframe(
    client: AsyncClient, auth_headers: Headers
) -> None:
    tutor = await _create_tutor(client, auth_headers)
    key = await _create_key(
        client, auth_headers, tutor["id"], allowed_origins=["https://cliente.com"]
    )

    response = await client.get(
        f"/api/v1/tutors/{tutor['id']}/embed-snippet?key_id={key['id']}", headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["embed_url"] == f"http://localhost:3000/embed/{key['public_key']}"
    assert "<iframe" in body["iframe_html"]
    assert body["embed_url"] in body["iframe_html"]
    # The integrator must be told the key is public; that is the whole security model.
    assert any("publica" in note for note in body["notes"])


async def test_snippet_warns_when_the_key_accepts_any_origin(
    client: AsyncClient, auth_headers: Headers, settings: Settings
) -> None:
    settings.embed_default_origins = ""
    tutor = await _create_tutor(client, auth_headers)
    key = await _create_key(client, auth_headers, tutor["id"])

    response = await client.get(
        f"/api/v1/tutors/{tutor['id']}/embed-snippet?key_id={key['id']}", headers=auth_headers
    )

    assert response.json()["allowed_origins"] == []
    assert any("qualquer origem" in note for note in response.json()["notes"])


async def test_snippet_of_a_key_from_another_tutor_is_not_found(
    client: AsyncClient, auth_headers: Headers
) -> None:
    owner = await _create_tutor(client, auth_headers, title="Dono")
    other = await _create_tutor(client, auth_headers, title="Outro")
    key = await _create_key(client, auth_headers, owner["id"])

    response = await client.get(
        f"/api/v1/tutors/{other['id']}/embed-snippet?key_id={key['id']}", headers=auth_headers
    )

    assert response.status_code == 404


# --- runtime authorisation -------------------------------------------------


@pytest.fixture
def embed_service(session: Any, tutor_service: TutorService, settings: Settings) -> EmbedService:
    return EmbedService(keys=EmbedKeyRepository(session), tutors=tutor_service, settings=settings)


async def _seed_key(
    tutor_service: TutorService, embed_service: EmbedService, origins: list[str]
) -> tuple[str, str]:
    from app.schemas.embed import EmbedKeyCreate
    from app.schemas.tutor import TutorCreate

    tutor = await tutor_service.create(
        TutorCreate(title="Tutor", system_instructions="Instrucoes suficientes.")
    )
    key = await embed_service.create_key(tutor.id, EmbedKeyCreate(allowed_origins=origins))
    return tutor.id, key.public_key


async def test_authorize_accepts_a_listed_origin(
    tutor_service: TutorService, embed_service: EmbedService
) -> None:
    tutor_id, public_key = await _seed_key(tutor_service, embed_service, ["https://cliente.com"])

    key, tutor = await embed_service.authorize(public_key, "https://cliente.com")

    assert tutor.id == tutor_id
    assert key.last_used_at is not None


async def test_authorize_rejects_an_unlisted_origin(
    tutor_service: TutorService, embed_service: EmbedService
) -> None:
    _, public_key = await _seed_key(tutor_service, embed_service, ["https://cliente.com"])

    with pytest.raises(OriginNotAllowedError):
        await embed_service.authorize(public_key, "https://atacante.com")


async def test_authorize_rejects_a_missing_origin_header(
    tutor_service: TutorService, embed_service: EmbedService
) -> None:
    _, public_key = await _seed_key(tutor_service, embed_service, ["https://cliente.com"])

    with pytest.raises(OriginNotAllowedError):
        await embed_service.authorize(public_key, None)


async def test_authorize_rejects_an_unknown_key(embed_service: EmbedService) -> None:
    with pytest.raises(EmbedKeyNotFoundError):
        await embed_service.authorize("pk_live_inexistente", "https://cliente.com")


async def test_authorize_rejects_a_revoked_key(
    session: Any, tutor_service: TutorService, embed_service: EmbedService
) -> None:
    _, public_key = await _seed_key(tutor_service, embed_service, ["https://cliente.com"])
    key = await EmbedKeyRepository(session).get_by_public_key(public_key)
    assert key is not None
    await embed_service.revoke_key(key.id)

    with pytest.raises(EmbedKeyRevokedError):
        await embed_service.authorize(public_key, "https://cliente.com")


async def test_a_key_without_an_allowlist_is_refused_outside_local(
    tutor_service: TutorService, settings: Settings, session: Any
) -> None:
    """The "any origin" escape hatch must not be reachable in a real environment."""
    from app.core.errors import ValidationError
    from app.schemas.embed import EmbedKeyCreate
    from app.schemas.tutor import TutorCreate

    production = settings.model_copy(update={"app_env": "production", "embed_default_origins": ""})
    service = EmbedService(
        keys=EmbedKeyRepository(session), tutors=tutor_service, settings=production
    )
    tutor = await tutor_service.create(
        TutorCreate(title="Tutor", system_instructions="Instrucoes suficientes.")
    )

    with pytest.raises(ValidationError, match="ao menos uma origem"):
        await service.create_key(tutor.id, EmbedKeyCreate())


async def test_authorize_rejects_an_inactive_tutor(
    tutor_service: TutorService, embed_service: EmbedService
) -> None:
    from app.db.models.tutor import TutorStatus

    tutor_id, public_key = await _seed_key(tutor_service, embed_service, ["https://cliente.com"])
    await tutor_service.set_status(tutor_id, TutorStatus.INACTIVE)

    with pytest.raises(TutorInactiveError):
        await embed_service.authorize(public_key, "https://cliente.com")
