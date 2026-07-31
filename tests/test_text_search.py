"""Document structuring and lexical retrieval.

This is the replacement for vector RAG (PRD 6.2), so it gets tested as the core feature it is.
"""

from __future__ import annotations

from app.utils.text import (
    MAX_CHUNK_CHARS,
    build_outline,
    chunk_document,
    normalize_whitespace,
    search_chunks,
    tokenize,
)

POLICY = """\
# Politica de Trabalho Remoto

Documento interno.

## Modelo hibrido
Colaboradores trabalham presencialmente as tercas e quartas-feiras.

## Auxilio home office
O auxilio e de R$ 150,00 por mes, pago junto ao salario.

## Ferias
As ferias seguem a CLT: 30 dias por periodo aquisitivo.
"""


def test_normalize_whitespace_keeps_paragraphs_but_collapses_runs() -> None:
    assert normalize_whitespace("a  b\n\n\n\nc   d\n") == "a b\n\nc d"


def test_outline_lists_sections_with_level_and_preview() -> None:
    outline = build_outline(POLICY)

    assert [section.heading for section in outline] == [
        "Politica de Trabalho Remoto",
        "Modelo hibrido",
        "Auxilio home office",
        "Ferias",
    ]
    assert outline[0].level == 1
    assert outline[1].level == 2
    assert "presencialmente" in outline[1].preview


def test_outline_of_a_document_without_headings_is_empty() -> None:
    assert build_outline("Apenas um paragrafo corrido, sem titulo nenhum.") == []


def test_chunking_uses_headings_as_boundaries() -> None:
    chunks = chunk_document(POLICY)

    headings = {chunk.heading for chunk in chunks}
    assert "Ferias" in headings
    assert all(chunk.text.strip() for chunk in chunks)


def test_chunking_splits_a_long_section() -> None:
    long_body = "\n\n".join(f"Paragrafo numero {index} com algum conteudo." for index in range(200))

    chunks = chunk_document(f"# Titulo\n\n{long_body}")

    assert len(chunks) > 1
    assert all(len(chunk.text) <= MAX_CHUNK_CHARS * 2 for chunk in chunks)


def test_chunking_handles_a_document_without_headings() -> None:
    chunks = chunk_document("Texto corrido sem nenhum titulo.")

    assert len(chunks) == 1
    assert chunks[0].heading == ""


def test_chunking_an_empty_document_returns_nothing() -> None:
    assert chunk_document("   \n\n  ") == []


# --- tokenisation ----------------------------------------------------------


def test_tokenize_folds_accents() -> None:
    """Without accent folding, "ferias" would never match "férias"."""
    assert tokenize("Férias") == tokenize("ferias") == ["ferias"]


def test_tokenize_drops_stopwords() -> None:
    assert tokenize("o auxilio de home office") == ["auxilio", "home", "office"]


# --- search ----------------------------------------------------------------


def test_search_finds_the_relevant_section() -> None:
    chunks = chunk_document(POLICY)

    results = search_chunks(chunks, "auxilio home office", limit=1)

    assert results
    assert "150,00" in results[0].chunk.text


def test_search_matches_despite_missing_accents_in_the_query() -> None:
    chunks = chunk_document(POLICY)

    results = search_chunks(chunks, "ferias CLT", limit=1)

    assert results
    assert "CLT" in results[0].chunk.text


def test_search_returns_nothing_for_an_unrelated_query() -> None:
    chunks = chunk_document(POLICY)

    assert search_chunks(chunks, "reembolso de quilometragem aeronautica", limit=3) == []


def test_search_respects_the_limit_and_orders_by_score() -> None:
    chunks = chunk_document(POLICY)

    results = search_chunks(chunks, "trabalho remoto ferias auxilio", limit=2)

    assert len(results) <= 2
    assert results == sorted(results, key=lambda scored: scored.score, reverse=True)


def test_search_on_an_empty_corpus_is_safe() -> None:
    assert search_chunks([], "qualquer coisa") == []


def test_search_with_a_stopword_only_query_is_safe() -> None:
    assert search_chunks(chunk_document(POLICY), "de o a") == []


def test_search_on_a_document_of_pure_symbols_does_not_divide_by_zero() -> None:
    """Regression: BM25 divides by the average document length, which is zero here."""
    chunks = chunk_document("### ---\n\n!!! ??? ***")

    assert search_chunks(chunks, "qualquer") == []


def test_search_works_on_a_single_chunk_document() -> None:
    """Regression: BM25 gives a *negative* score to a term present in the whole corpus.

    On a one-chunk document every match scores below zero, so filtering by `score > 0` made
    short sources — an inline FAQ, a small policy — silently unsearchable.
    """
    chunks = chunk_document("# Nota\n\nO reembolso e solicitado pelo portal ate o dia 5.")
    assert len(chunks) == 1

    results = search_chunks(chunks, "reembolso")

    assert len(results) == 1
    assert "portal" in results[0].chunk.text


def test_a_short_document_still_rejects_an_unrelated_query() -> None:
    chunks = chunk_document("# Nota\n\nO reembolso e solicitado pelo portal.")

    assert search_chunks(chunks, "criptografia quantica") == []
