"""Tutor management API (PRD 3.1 and 4.1.2)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

Headers = dict[str, str]


def _tutor_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Tutor de Onboarding",
        "description": "Ajuda novos colaboradores",
        "system_instructions": "Voce e um tutor de onboarding. Use as fontes configuradas.",
    }
    payload.update(overrides)
    return payload


async def _create(client: AsyncClient, headers: Headers, **overrides: Any) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/tutors", json=_tutor_payload(**overrides), headers=headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- authentication --------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/tutors"),
        ("post", "/api/v1/tutors"),
        ("get", "/api/v1/tutors/qualquer-id"),
        ("patch", "/api/v1/tutors/qualquer-id"),
        ("delete", "/api/v1/tutors/qualquer-id"),
        ("post", "/api/v1/tutors/qualquer-id/deactivate"),
        ("post", "/api/v1/tutors/qualquer-id/sources"),
    ],
)
async def test_every_management_route_requires_an_admin_token(
    client: AsyncClient, method: str, path: str
) -> None:
    # `client.request` instead of `client.get`/`client.delete`: httpx refuses a body on those.
    response = await client.request(method.upper(), path, json={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


# --- create ----------------------------------------------------------------


async def test_create_persists_and_returns_the_tutor(
    client: AsyncClient, auth_headers: Headers
) -> None:
    body = await _create(client, auth_headers)

    assert body["slug"] == "tutor-de-onboarding"
    assert body["status"] == "active"
    assert body["sources"] == []

    stored = await client.get(f"/api/v1/tutors/{body['id']}", headers=auth_headers)
    assert stored.status_code == 200
    assert stored.json()["title"] == "Tutor de Onboarding"


async def test_create_with_sources(client: AsyncClient, auth_headers: Headers) -> None:
    body = await _create(
        client,
        auth_headers,
        sources=[
            {"kind": "url", "label": "Guia", "url": "https://example.com/guia.md"},
            {"kind": "inline_text", "label": "FAQ", "content": "Pergunta e resposta."},
        ],
    )

    assert [source["label"] for source in body["sources"]] == ["Guia", "FAQ"]
    assert body["sources"][0]["url"] == "https://example.com/guia.md"


async def test_create_rejects_a_duplicate_explicit_slug(
    client: AsyncClient, auth_headers: Headers
) -> None:
    await _create(client, auth_headers, slug="suporte")

    response = await client.post(
        "/api/v1/tutors", json=_tutor_payload(slug="suporte"), headers=auth_headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TUTOR_SLUG_TAKEN"


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "T"},
        {"title": "Tutor", "system_instructions": "curto"},
        {"title": "Tutor", "system_instructions": "Instrucoes ok.", "campo": "extra"},
        {
            "title": "Tutor",
            "system_instructions": "Instrucoes ok.",
            "sources": [{"kind": "url", "label": "Sem url"}],
        },
    ],
    ids=["sem-instrucoes", "instrucoes-curtas", "campo-desconhecido", "fonte-url-sem-url"],
)
async def test_create_validates_the_payload(
    client: AsyncClient, auth_headers: Headers, payload: dict[str, Any]
) -> None:
    response = await client.post("/api/v1/tutors", json=payload, headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["fields"]


# --- read ------------------------------------------------------------------


async def test_get_unknown_tutor_returns_404(client: AsyncClient, auth_headers: Headers) -> None:
    response = await client.get("/api/v1/tutors/nao-existe", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TUTOR_NOT_FOUND"


async def test_list_supports_search_status_and_pagination(
    client: AsyncClient, auth_headers: Headers
) -> None:
    await _create(client, auth_headers, title="Tutor de Vendas")
    inactive = await _create(client, auth_headers, title="Tutor de Suporte")
    await client.post(f"/api/v1/tutors/{inactive['id']}/deactivate", headers=auth_headers)

    listed = await client.get("/api/v1/tutors", headers=auth_headers)
    active_only = await client.get("/api/v1/tutors?status=active", headers=auth_headers)
    searched = await client.get("/api/v1/tutors?q=suporte", headers=auth_headers)
    paginated = await client.get("/api/v1/tutors?page=1&size=1", headers=auth_headers)

    assert listed.json()["total"] == 2
    assert active_only.json()["total"] == 1
    assert active_only.json()["items"][0]["title"] == "Tutor de Vendas"
    assert searched.json()["total"] == 1
    assert len(paginated.json()["items"]) == 1


async def test_list_rejects_an_unknown_status_filter(
    client: AsyncClient, auth_headers: Headers
) -> None:
    response = await client.get("/api/v1/tutors?status=arquivado", headers=auth_headers)

    assert response.status_code == 422


# --- update and status -----------------------------------------------------


async def test_patch_updates_only_the_sent_fields(
    client: AsyncClient, auth_headers: Headers
) -> None:
    created = await _create(client, auth_headers)

    response = await client.patch(
        f"/api/v1/tutors/{created['id']}",
        json={"title": "Tutor Renomeado"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Tutor Renomeado"
    assert response.json()["system_instructions"] == created["system_instructions"]
    assert response.json()["slug"] == created["slug"]


async def test_deactivate_and_activate_round_trip(
    client: AsyncClient, auth_headers: Headers
) -> None:
    created = await _create(client, auth_headers)

    deactivated = await client.post(
        f"/api/v1/tutors/{created['id']}/deactivate", headers=auth_headers
    )
    reactivated = await client.post(
        f"/api/v1/tutors/{created['id']}/activate", headers=auth_headers
    )

    assert deactivated.json()["status"] == "inactive"
    assert reactivated.json()["status"] == "active"


async def test_delete_removes_the_tutor(client: AsyncClient, auth_headers: Headers) -> None:
    created = await _create(client, auth_headers)

    deleted = await client.delete(f"/api/v1/tutors/{created['id']}", headers=auth_headers)
    fetched = await client.get(f"/api/v1/tutors/{created['id']}", headers=auth_headers)

    assert deleted.status_code == 204
    assert fetched.status_code == 404


# --- sources ---------------------------------------------------------------


async def test_add_and_remove_a_source(client: AsyncClient, auth_headers: Headers) -> None:
    created = await _create(client, auth_headers)

    added = await client.post(
        f"/api/v1/tutors/{created['id']}/sources",
        json={"kind": "inline_text", "label": "Politica", "content": "Texto da politica."},
        headers=auth_headers,
    )
    assert added.status_code == 201

    detail = await client.get(f"/api/v1/tutors/{created['id']}", headers=auth_headers)
    assert len(detail.json()["sources"]) == 1

    removed = await client.delete(
        f"/api/v1/tutors/{created['id']}/sources/{added.json()['id']}", headers=auth_headers
    )
    assert removed.status_code == 204

    after = await client.get(f"/api/v1/tutors/{created['id']}", headers=auth_headers)
    assert after.json()["sources"] == []


async def test_source_limit_is_enforced_by_the_api(
    client: AsyncClient, auth_headers: Headers
) -> None:
    created = await _create(client, auth_headers)

    for index in range(10):
        response = await client.post(
            f"/api/v1/tutors/{created['id']}/sources",
            json={"kind": "inline_text", "label": f"Fonte {index}", "content": "texto"},
            headers=auth_headers,
        )
        assert response.status_code == 201

    overflow = await client.post(
        f"/api/v1/tutors/{created['id']}/sources",
        json={"kind": "inline_text", "label": "Excedente", "content": "texto"},
        headers=auth_headers,
    )

    assert overflow.status_code == 409
    assert overflow.json()["error"]["code"] == "SOURCE_LIMIT_REACHED"


async def test_removing_a_source_from_another_tutor_returns_404(
    client: AsyncClient, auth_headers: Headers
) -> None:
    owner = await _create(client, auth_headers, title="Dono")
    other = await _create(client, auth_headers, title="Outro")
    added = await client.post(
        f"/api/v1/tutors/{owner['id']}/sources",
        json={"kind": "inline_text", "label": "F", "content": "t"},
        headers=auth_headers,
    )

    response = await client.delete(
        f"/api/v1/tutors/{other['id']}/sources/{added.json()['id']}", headers=auth_headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SOURCE_NOT_FOUND"
