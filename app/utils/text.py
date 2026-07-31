"""Document structuring and lexical search.

This module is the whole of the "retrieval" in this project. There is no vector index, no
embedding model and no similarity search (PRD 6.2): a document is split along its own headings
and paragraphs, and ranked against a query with **BM25**, a keyword statistic.

The intelligence is not here — it is in the agent, which decides *which* source to open and
*what* to search for. This layer only has to be fast, predictable and free of hidden state.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from rank_bm25 import BM25Okapi

_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*$", re.MULTILINE)
_TOKEN = re.compile(r"[a-z0-9]+")
_WHITESPACE = re.compile(r"[ \t]+")

MIN_CHUNK_CHARS: Final = 200
MAX_CHUNK_CHARS: Final = 1_200

# Portuguese and English function words. Dropping them keeps BM25 from ranking a chunk highly
# just because it repeats "de" or "the" as often as the query does.
_STOPWORDS: Final = frozenset(
    [
        "a",
        "as",
        "o",
        "os",
        "um",
        "uma",
        "uns",
        "umas",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "por",
        "para",
        "com",
        "sem",
        "sob",
        "sobre",
        "e",
        "ou",
        "mas",
        "que",
        "se",
        "ao",
        "aos",
        "à",
        "às",
        "pelo",
        "pela",
        "pelos",
        "pelas",
        "este",
        "esta",
        "esse",
        "essa",
        "aquele",
        "aquela",
        "isso",
        "isto",
        "aquilo",
        "eu",
        "tu",
        "ele",
        "ela",
        "nos",
        "vos",
        "eles",
        "elas",
        "meu",
        "minha",
        "seu",
        "sua",
        "nosso",
        "nossa",
        "qual",
        "quais",
        "quando",
        "onde",
        "como",
        "porque",
        "entao",
        "ja",
        "nao",
        "sim",
        "ser",
        "estar",
        "ter",
        "haver",
        "foi",
        "era",
        "sao",
        "eh",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "without",
        "by",
        "from",
        "and",
        "or",
        "but",
        "that",
        "this",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "as",
        "at",
    ]
)


@dataclass(frozen=True, slots=True)
class Section:
    """A heading and the position where its content starts."""

    heading: str
    level: int
    start: int
    preview: str


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    heading: str
    text: str
    start: int


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: Chunk
    score: float


def normalize_whitespace(text: str) -> str:
    """Collapse horizontal whitespace and blank-line runs, keeping paragraph breaks."""
    lines = [_WHITESPACE.sub(" ", line).rstrip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def build_outline(text: str, *, max_sections: int = 60) -> list[Section]:
    """Extract the markdown heading structure.

    The outline is what lets the agent *navigate* a document instead of reading all of it: it
    can look at the section titles, then search or open only the relevant part.
    """
    sections: list[Section] = []
    for match in _HEADING.finditer(text):
        start = match.end()
        preview = normalize_whitespace(text[start : start + 160]).replace("\n", " ")
        sections.append(
            Section(
                heading=match.group("title").strip(),
                level=len(match.group("hashes")),
                start=start,
                preview=preview,
            )
        )
        if len(sections) >= max_sections:
            break
    return sections


def chunk_document(text: str) -> list[Chunk]:
    """Split a document into retrievable chunks.

    Headings are used as boundaries when present, because they are the author's own semantic
    split. Long sections are further divided on paragraph breaks, and very short consecutive
    pieces are merged so a chunk carries enough context to be useful as an answer.
    """
    text = normalize_whitespace(text)
    if not text:
        return []

    blocks = _split_by_heading(text) or [("", text)]

    chunks: list[Chunk] = []
    for heading, body in blocks:
        for piece, offset in _split_long_block(body, base_offset=text.find(body)):
            chunks.append(
                Chunk(index=len(chunks), heading=heading, text=piece, start=max(offset, 0))
            )

    return _merge_small_chunks(chunks)


def _split_by_heading(text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING.finditer(text))
    if not matches:
        return []

    blocks: list[tuple[str, str]] = []

    preamble = text[: matches[0].start()].strip()
    if preamble:
        blocks.append(("", preamble))

    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if body:
            blocks.append((match.group("title").strip(), body))
    return blocks


def _split_long_block(body: str, *, base_offset: int) -> list[tuple[str, int]]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [(body, base_offset)]

    pieces: list[tuple[str, int]] = []
    current: list[str] = []
    current_len = 0
    cursor = base_offset

    for paragraph in body.split("\n\n"):
        if current_len + len(paragraph) > MAX_CHUNK_CHARS and current:
            joined = "\n\n".join(current)
            pieces.append((joined, cursor))
            cursor += len(joined) + 2
            current, current_len = [], 0
        current.append(paragraph)
        current_len += len(paragraph) + 2

    if current:
        pieces.append(("\n\n".join(current), cursor))
    return pieces


def _merge_small_chunks(chunks: list[Chunk]) -> list[Chunk]:
    merged: list[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and len(merged[-1].text) < MIN_CHUNK_CHARS
            and merged[-1].heading == chunk.heading
        ):
            previous = merged.pop()
            chunk = Chunk(
                index=previous.index,
                heading=previous.heading,
                text=f"{previous.text}\n\n{chunk.text}",
                start=previous.start,
            )
        merged.append(
            Chunk(index=len(merged), heading=chunk.heading, text=chunk.text, start=chunk.start)
        )
    return merged


def tokenize(text: str) -> list[str]:
    """Lowercase, strip accents, drop stopwords.

    Accent folding matters in Portuguese: without it "ferias" would not match "férias", and the
    agent's queries rarely carry the right diacritics.
    """
    folded = unicodedata.normalize("NFKD", text)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    return [token for token in _TOKEN.findall(ascii_only) if token not in _STOPWORDS]


def search_chunks(chunks: list[Chunk], query: str, *, limit: int = 3) -> list[ScoredChunk]:
    """Rank chunks against a query with BM25. Lexical, not semantic — and that is the point.

    Relevance is decided by **token overlap**, not by the sign of the BM25 score. BM25's IDF
    term goes negative when a word appears in most of the corpus, which on a small document
    (one or two chunks — an inline FAQ, a short policy) is the normal case: every match would
    score below zero and a naive `score > 0` filter would answer "nothing found" while sitting
    on the answer. BM25 is used for *ordering* the candidates; overlap decides who is a
    candidate at all.
    """
    if not chunks:
        return []

    query_tokens = set(tokenize(query))
    if not query_tokens:
        return []

    corpus = [tokenize(f"{chunk.heading}\n{chunk.text}") for chunk in chunks]
    # BM25Okapi divides by the average document length, which is zero if every chunk tokenises
    # to nothing (a document of pure stopwords or symbols).
    if not any(corpus):
        return []

    scores = BM25Okapi(corpus).get_scores(sorted(query_tokens))

    candidates = [
        ScoredChunk(chunk=chunk, score=float(score))
        for chunk, tokens, score in zip(chunks, corpus, scores, strict=True)
        if query_tokens.intersection(tokens)
    ]
    candidates.sort(key=lambda scored: scored.score, reverse=True)
    return candidates[:limit]
