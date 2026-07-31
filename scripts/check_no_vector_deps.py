"""Guard for PRD section 6.2: no vector database, vector index or embedding model.

The knowledge strategy of this project is *agentic* — the LLM decides what to look up through
tools, and retrieval inside a document is lexical (BM25). Classic vector RAG is explicitly out of
scope, and acceptance criterion 7.4 requires that no such dependency is core to the project.

This script fails the build if a banned package is declared or a banned symbol is imported by
application code. It runs in pre-commit and in CI.

Usage: python scripts/check_no_vector_deps.py
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BANNED_PACKAGES = frozenset(
    {
        "chromadb",
        "faiss",
        "faiss-cpu",
        "faiss-gpu",
        "pgvector",
        "pinecone",
        "pinecone-client",
        "qdrant-client",
        "weaviate-client",
        "milvus",
        "pymilvus",
        "lancedb",
        "sentence-transformers",
        "txtai",
        "annoy",
        "hnswlib",
        "usearch",
        "redisvl",
        "llama-index",
        "sqlite-vec",
        "sqlite-vss",
    }
)

# Symbols that would mean embeddings crept into the runtime, even without a new dependency
# (pydantic-ai ships an embeddings API we deliberately do not use).
BANNED_SYMBOLS = (
    re.compile(r"\bfrom\s+pydantic_ai\.embeddings\b"),
    re.compile(r"\bfrom\s+pydantic_ai\s+import\s+[^\n]*\b(Embedder|EmbeddingModel)\b"),
    re.compile(r"\bembed_content\b"),
    re.compile(r"\bOpenAIEmbeddings\b"),
    re.compile(r"\bGoogleGenerativeAIEmbeddings\b"),
)

_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def _requirement_name(requirement: str) -> str:
    match = _REQUIREMENT_NAME.match(requirement.strip())
    return match.group(0).lower().replace("_", "-") if match else ""


def check_dependencies() -> list[str]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})

    declared: list[str] = list(project.get("dependencies", []))
    for extra_requirements in project.get("optional-dependencies", {}).values():
        declared.extend(extra_requirements)

    return [
        f"pyproject.toml declares banned dependency: {requirement!r}"
        for requirement in declared
        if _requirement_name(requirement) in BANNED_PACKAGES
    ]


def check_imports() -> list[str]:
    violations: list[str] = []
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        content = path.read_text(encoding="utf-8")
        for pattern in BANNED_SYMBOLS:
            if pattern.search(content):
                relative = path.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{relative} uses a banned embedding symbol: {pattern.pattern}")
    return violations


def main() -> int:
    violations = check_dependencies() + check_imports()
    if violations:
        print("Estrategia de conhecimento violada (PRD 6.2 / criterio 7.4):", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        print(
            "\nA recuperacao deve permanecer agentica (tools + BM25 lexical), "
            "sem banco vetorial nem embeddings.",
            file=sys.stderr,
        )
        return 1

    print("OK: nenhuma dependencia de banco vetorial ou embedding encontrada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
