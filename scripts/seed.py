"""Seed the database with an administrator and demo tutors.

Run with `python -m scripts.seed`. Idempotent: running it twice does not duplicate anything,
so it is safe to call after every `alembic upgrade head`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.models.admin import AdminRole, AdminUser
from app.db.models.embed import EmbedKey
from app.db.models.tutor import SourceKind, Tutor, TutorSource, TutorStatus
from app.db.session import create_engine, create_session_factory
from app.services.embed_service import generate_public_key

logger = get_logger("scripts.seed")


@dataclass(frozen=True, slots=True)
class SourceSpec:
    kind: SourceKind
    label: str
    url: str | None = None
    content: str | None = None


@dataclass(frozen=True, slots=True)
class TutorSpec:
    title: str
    slug: str
    description: str
    greeting: str
    system_instructions: str
    sources: tuple[SourceSpec, ...]


# Public, stable, plain-text documents. Chosen so the demo works without any private asset and
# so the agent has something real to search through.
DEMO_TUTORS: tuple[TutorSpec, ...] = (
    TutorSpec(
        title="Tutor de Onboarding Python",
        slug="onboarding-python",
        description="Tira duvidas sobre a linguagem com base na documentacao oficial.",
        greeting="Ola! Sou o tutor de onboarding. Pergunte algo sobre Python.",
        system_instructions=(
            "Voce e um tutor tecnico de onboarding em Python. Responda sempre em portugues do "
            "Brasil, em tom direto e didatico.\n\n"
            "Antes de responder qualquer pergunta sobre conteudo, consulte as fontes "
            "disponiveis com as ferramentas. Nunca invente uma informacao que nao esteja nas "
            "fontes: se a resposta nao estiver la, diga isso claramente e sugira o que o "
            "usuario poderia perguntar.\n\n"
            "Cite a fonte usada ao final de cada resposta baseada em conteudo."
        ),
        sources=(
            SourceSpec(
                kind=SourceKind.URL,
                label="PEP 20 - Zen of Python",
                url="https://peps.python.org/pep-0020/",
            ),
            SourceSpec(
                kind=SourceKind.URL,
                label="PEP 8 - Guia de estilo",
                url="https://peps.python.org/pep-0008/",
            ),
        ),
    ),
    TutorSpec(
        title="Tutor de Politicas Internas",
        slug="politicas-internas",
        description="Responde sobre ferias, home office e reembolso com base na politica.",
        greeting="Oi! Posso ajudar com duvidas sobre as politicas internas.",
        system_instructions=(
            "Voce e um assistente de RH. Responda em portugues do Brasil, de forma objetiva e "
            "cordial.\n\n"
            "Use exclusivamente o conteudo das fontes configuradas. Se a politica nao cobrir a "
            "duvida, diga que nao consta e oriente a pessoa a procurar o RH. Nunca deduza "
            "valores, prazos ou excecoes que nao estejam escritos."
        ),
        sources=(
            SourceSpec(
                kind=SourceKind.INLINE_TEXT,
                label="Politica de trabalho remoto (ficticia)",
                content=(
                    "# Politica de Trabalho Remoto\n\n"
                    "## Modelo hibrido\n"
                    "Colaboradores trabalham presencialmente as tercas e quartas-feiras. "
                    "Os demais dias sao de livre escolha entre casa e escritorio.\n\n"
                    "## Auxilio home office\n"
                    "O auxilio e de R$ 150,00 por mes, pago junto ao salario, para quem atua "
                    "em regime hibrido ou remoto integral.\n\n"
                    "## Trabalho remoto do exterior\n"
                    "Permitido por ate 30 dias corridos por ano, mediante aprovacao previa da "
                    "lideranca direta e do RH, com antecedencia minima de 15 dias.\n\n"
                    "## Equipamento\n"
                    "A empresa fornece notebook e headset. Cadeira e monitor podem ser "
                    "solicitados uma vez a cada 36 meses.\n\n"
                    "## Ferias\n"
                    "As ferias seguem a CLT: 30 dias por periodo aquisitivo, podendo ser "
                    "divididas em ate tres periodos, sendo um deles de no minimo 14 dias."
                ),
            ),
        ),
    ),
)


async def seed_admin(session: AsyncSession, settings: Settings) -> AdminUser:
    email = settings.seed_admin_email.strip().lower()
    existing = await session.execute(select(AdminUser).where(AdminUser.email == email))
    admin = existing.scalar_one_or_none()

    if admin is not None:
        logger.info("seed_admin_exists", admin_id=admin.id)
        return admin

    admin = AdminUser(
        email=email,
        password_hash=hash_password(settings.seed_admin_password),
        role=AdminRole.ADMIN,
    )
    session.add(admin)
    await session.flush()
    logger.info("seed_admin_created", admin_id=admin.id, email=email)
    return admin


async def seed_tutors(session: AsyncSession) -> list[Tutor]:
    created: list[Tutor] = []

    for spec in DEMO_TUTORS:
        existing = await session.execute(select(Tutor).where(Tutor.slug == spec.slug))
        if existing.scalar_one_or_none() is not None:
            logger.info("seed_tutor_exists", slug=spec.slug)
            continue

        tutor = Tutor(
            title=spec.title,
            slug=spec.slug,
            description=spec.description,
            greeting=spec.greeting,
            system_instructions=spec.system_instructions,
            status=TutorStatus.ACTIVE,
            model_settings={},
        )
        tutor.sources.extend(
            TutorSource(
                kind=source.kind,
                label=source.label,
                url=source.url,
                content=source.content,
            )
            for source in spec.sources
        )

        session.add(tutor)
        created.append(tutor)
        logger.info("seed_tutor_created", slug=spec.slug)

    await session.flush()
    return created


async def seed_embed_key(session: AsyncSession, settings: Settings) -> EmbedKey | None:
    """Give the first demo tutor a ready-to-use embed key.

    Without this, seeing the widget means logging in, creating a key, copying it into
    `.env.local` and restarting the frontend — four manual steps before anything is visible.
    The key is public by design, so seeding one leaks nothing.
    """
    tutor = (
        await session.execute(select(Tutor).where(Tutor.slug == DEMO_TUTORS[0].slug))
    ).scalar_one_or_none()
    if tutor is None:  # pragma: no cover - only if the tutor seed was removed
        return None

    existing = (
        (await session.execute(select(EmbedKey).where(EmbedKey.tutor_id == tutor.id)))
        .scalars()
        .first()
    )
    if existing is not None:
        logger.info("seed_embed_key_exists", embed_key_id=existing.id)
        return existing

    key = EmbedKey(
        tutor_id=tutor.id,
        public_key=generate_public_key(),
        label="Chave de demonstracao",
        allowed_origins=list(settings.default_embed_origins),
        is_active=True,
    )
    session.add(key)
    await session.flush()
    logger.info("seed_embed_key_created", embed_key_id=key.id, tutor_id=tutor.id)
    return key


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=False)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        await seed_admin(session, settings)
        await seed_tutors(session)
        key = await seed_embed_key(session, settings)
        public_key = key.public_key if key else None
        await session.commit()

    await engine.dispose()

    print("\nSeed concluido.")
    print(f"  Admin: {settings.seed_admin_email}")
    print("  Senha: definida em SEED_ADMIN_PASSWORD")
    print(f"  Tutores: {', '.join(spec.slug for spec in DEMO_TUTORS)}")

    if public_key:
        frontend = settings.frontend_base_url.rstrip("/")
        print(f"\n  Chave de embed: {public_key}")
        print(f"  Widget:         {frontend}/embed/{public_key}")
        print(f"  Demonstracao:   {frontend}/demo?key={public_key}")
        print(f"  Origens:        {', '.join(settings.default_embed_origins) or 'qualquer (dev)'}")


if __name__ == "__main__":
    asyncio.run(main())
