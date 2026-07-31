"""The agent's knowledge tools (PRD 4.3.2).

These four functions *are* the knowledge strategy. There is no vector database, no embedding
model and no retrieval pipeline running before the model: the agent chooses which source to
open and what to look for, one call at a time.

They are written as plain async functions over `SourceService` and registered by the runner, so
the same implementations serve any agent framework.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.agent.contracts import AgentDeps, Citation, ToolInvocation
from app.agent.prompts import wrap_source_content
from app.core.errors import SourceNotFoundError
from app.core.logging import get_logger
from app.db.models.tutor import TutorSource

logger = get_logger(__name__)

MAX_READ_CHARS = 4_000
MAX_SNIPPETS = 5


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """Text handed back to the model, plus what to record about the call."""

    text: str
    invocation: ToolInvocation


async def list_sources(deps: AgentDeps) -> ToolOutcome:
    """Catalogue of the sources configured for this tutor."""
    started = time.perf_counter()
    infos = await deps.sources.list_sources(deps.tutor.id)

    if not infos:
        body = "Este tutor nao tem fontes configuradas."
    else:
        lines = []
        for info in infos:
            status = "disponivel" if info.available else f"indisponivel ({info.error})"
            lines.append(
                f'- id="{info.source_id}" | {info.label} | {info.characters} caracteres | '
                f"{info.section_count} secoes | {status}"
            )
        body = "\n".join(lines)

    return _outcome(
        deps,
        name="list_sources",
        source_label=None,
        started=started,
        ok=True,
        text=body,
    )


async def get_source_outline(deps: AgentDeps, source_id: str) -> ToolOutcome:
    """Section titles of a source, so the agent can navigate before reading."""
    started = time.perf_counter()

    source = await _resolve(deps, source_id)
    if source is None:
        return _unknown_source(deps, "get_source_outline", source_id, started)

    loaded = await deps.sources.load(source)
    if not loaded.available:
        return _unavailable(deps, "get_source_outline", source, started, loaded.error)

    if not loaded.outline:
        body = (
            "Este documento nao tem secoes com titulo. Use search_source para procurar por "
            "palavras-chave ou fetch_source para ler sequencialmente."
        )
    else:
        body = "\n".join(
            f"{'  ' * (int(str(section.get('level', 1))) - 1)}- {section.get('heading')}"
            f" — {section.get('preview')}"
            for section in loaded.outline
        )

    return _outcome(
        deps,
        name="get_source_outline",
        source_label=source.label,
        started=started,
        ok=True,
        text=wrap_source_content(label=source.label, source_id=source.id, body=body),
    )


async def search_source(
    deps: AgentDeps, source_id: str, query: str, max_snippets: int = 3
) -> ToolOutcome:
    """Keyword (BM25) search inside one source. Lexical by design — no embeddings."""
    started = time.perf_counter()

    source = await _resolve(deps, source_id)
    if source is None:
        return _unknown_source(deps, "search_source", source_id, started)

    limit = max(1, min(max_snippets, MAX_SNIPPETS))
    results = await deps.sources.search(source, query, limit=limit)

    if not results:
        body = (
            f"Nenhum trecho encontrado para {query!r}. Tente outras palavras-chave, consulte "
            "get_source_outline para ver os assuntos cobertos, ou procure em outra fonte."
        )
        return _outcome(
            deps,
            name="search_source",
            source_label=source.label,
            started=started,
            ok=True,
            text=body,
            detail=f"0 trechos para {query!r}",
        )

    body = "\n\n".join(
        f"[trecho {position}{_section_suffix(scored.chunk.heading)}]\n{scored.chunk.text}"
        for position, scored in enumerate(results, start=1)
    )

    for scored in results:
        _remember_citation(
            deps, source_id=source.id, label=source.label, url=source.url, snippet=scored.chunk.text
        )

    return _outcome(
        deps,
        name="search_source",
        source_label=source.label,
        started=started,
        ok=True,
        text=wrap_source_content(label=source.label, source_id=source.id, body=body),
        detail=f"{len(results)} trechos para {query!r}",
    )


async def fetch_source(
    deps: AgentDeps, source_id: str, offset: int = 0, max_chars: int = MAX_READ_CHARS
) -> ToolOutcome:
    """Sequential paginated read, for when the agent needs the running text."""
    started = time.perf_counter()

    source = await _resolve(deps, source_id)
    if source is None:
        return _unknown_source(deps, "fetch_source", source_id, started)

    limit = max(500, min(max_chars, MAX_READ_CHARS))
    text, next_offset, has_more = await deps.sources.read(
        source, offset=max(0, offset), max_chars=limit
    )

    if not text:
        return _unavailable(deps, "fetch_source", source, started, "conteudo vazio ou fim do texto")

    continuation = (
        f"\n\n[Continua. Para ler o proximo trecho, chame fetch_source com offset={next_offset}.]"
        if has_more
        else "\n\n[Fim do documento.]"
    )

    _remember_citation(deps, source_id=source.id, label=source.label, url=source.url, snippet=text)

    return _outcome(
        deps,
        name="fetch_source",
        source_label=source.label,
        started=started,
        ok=True,
        text=wrap_source_content(label=source.label, source_id=source.id, body=text + continuation),
        detail=f"offset={offset} chars={len(text)}",
    )


# --- helpers ---------------------------------------------------------------


def _section_suffix(heading: str) -> str:
    return f" — secao: {heading}" if heading else ""


async def _resolve(deps: AgentDeps, source_id: str) -> TutorSource | None:
    try:
        return await deps.sources.get_source(deps.tutor.id, source_id.strip())
    except SourceNotFoundError:
        return None


def _remember_citation(
    deps: AgentDeps, *, source_id: str, label: str, url: str | None, snippet: str
) -> None:
    """Keep the first snippet used per source; the UI shows one chip per source, not per chunk."""
    deps.citations.setdefault(
        source_id,
        Citation(
            source_id=source_id,
            label=label,
            url=url,
            snippet=snippet[:280].strip(),
        ),
    )


def _outcome(
    deps: AgentDeps,
    *,
    name: str,
    source_label: str | None,
    started: float,
    ok: bool,
    text: str,
    detail: str | None = None,
) -> ToolOutcome:
    duration_ms = int((time.perf_counter() - started) * 1000)
    invocation = ToolInvocation(
        name=name, source_label=source_label, duration_ms=duration_ms, ok=ok, detail=detail
    )
    deps.invocations.append(invocation)

    logger.info(
        "tool_call",
        tool=name,
        tutor_id=deps.tutor.id,
        session_id=deps.session_id,
        source=source_label,
        duration_ms=duration_ms,
        ok=ok,
        detail=detail,
        result_chars=len(text),
    )
    return ToolOutcome(text=text, invocation=invocation)


def _unknown_source(deps: AgentDeps, tool_name: str, source_id: str, started: float) -> ToolOutcome:
    return _outcome(
        deps,
        name=tool_name,
        source_label=None,
        started=started,
        ok=False,
        text=(
            f'Nao existe fonte com id="{source_id}" neste tutor. Chame list_sources para ver '
            "os identificadores validos."
        ),
        detail="fonte desconhecida",
    )


def _unavailable(
    deps: AgentDeps,
    tool_name: str,
    source: TutorSource,
    started: float,
    error: str | None,
) -> ToolOutcome:
    return _outcome(
        deps,
        name=tool_name,
        source_label=source.label,
        started=started,
        ok=False,
        text=(
            f'A fonte "{source.label}" nao esta disponivel no momento'
            f"{f' ({error})' if error else ''}. Informe isso ao usuario e siga com as demais."
        ),
        detail=error,
    )
