# AGENTS.md — diretrizes para agentes de codificação

Contrato de trabalho para qualquer agente de IA que altere este repositório. O PRD do desafio
(§2) **exige** que o desenvolvimento seja assistido por agentes; este arquivo é o que mantém o
resultado coerente entre iterações.

---

## Contexto do projeto

Backend de uma plataforma onde administradores criam **tutores** (persona + instruções + fontes de
conhecimento) e os distribuem para sites de terceiros através de um **widget em `<iframe>`**.

Stack: Python 3.12 · FastAPI · SQLAlchemy 2.0 async · Alembic · **Pydantic AI** · Google Gemini.

---

## Regras invioláveis

1. **Proibido banco vetorial, índice vetorial ou embeddings.** O PRD (§6.2) veta RAG clássico como
   estratégia de conhecimento. Nenhuma dependência de `faiss`, `chromadb`, `pgvector`,
   `sentence-transformers`, `qdrant`, `weaviate`, `pinecone` ou similar pode entrar no
   `pyproject.toml` — há uma checagem no CI que falha o build se isso acontecer. A recuperação é
   **agêntica** (o LLM decide o que buscar via tools) com busca **lexical BM25**.
2. **Nenhum segredo no repositório.** Chaves apenas via variável de ambiente; `.env.example` só com
   placeholders.
3. **Respostas de erro nunca vazam stack trace** (PRD §5.1). Toda exceção sai pelos handlers globais
   de `app/core/errors.py`, no formato `{"error": {"code", "message", "request_id"}}`.
4. **Toda feature entra com teste.** Nenhuma funcionalidade é considerada pronta porque "parece
   certa" — a validação é `pytest` verde.
5. **Não adicionar dependência não solicitada.** Se uma nova biblioteca parecer necessária,
   justifique no PR/commit antes de instalar.
6. **Não introduzir LTI, integração com LMS, pagamento ou multi-tenant forte** — explicitamente fora
   de escopo (PRD §6).

---

## Convenções

- **Camadas:** `api` (HTTP) → `services` (regra de negócio) → `repositories` (dados) → `db.models`.
  Rotas não acessam o ORM diretamente; serviços não conhecem objetos de request/response HTTP.
- **O agente fica isolado** em `app/agent/`, atrás do protocolo `AgentRunner`. Nenhum outro módulo
  de `app/` importa `pydantic_ai` — é o que mantém a decisão D1 reversível. Os testes importam, de
  propósito: é de lá que vem o `FunctionModel`.
- **Tipagem obrigatória:** `mypy --strict` precisa passar. Sem `Any` gratuito, sem `# type: ignore`
  sem comentário explicando.
- **Async em todo I/O.** Nada de `requests`, `time.sleep` ou driver síncrono de banco. Chamada de
  biblioteca que bloqueia e não tem versão async (`socket.getaddrinfo`, por exemplo) vai para
  `asyncio.to_thread` — bloquear o event loop atrasa todas as conversas do processo, não só a que
  está esperando.
- **Idioma:** código, identificadores e docstrings em inglês; documentação (`README`, `docs/`) e
  mensagens voltadas ao usuário final em português.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`,
  `ci:`), atômicos, em português no corpo quando ajudar. Sem commits gigantes de "tudo pronto".

---

## Antes de dar uma tarefa por concluída

```bash
ruff check . && ruff format --check .
mypy app scripts
pytest
python scripts/check_no_vector_deps.py
```

Os três precisam passar. Se algum falhar, a tarefa não está pronta.

---

## Registro de iterações

Alterações relevantes — especialmente quando a saída do agente foi **rejeitada ou corrigida** —
devem ser registradas em [`docs/agent-log.md`](docs/agent-log.md). Esse registro é entregável
avaliado (PRD §2 e §7.6).
