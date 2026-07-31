# DOT Tutors — Backend

Backend do desafio técnico **Plataforma de Tutores Personalizados** (DOT Digital Group, PRD
`20260520_DOT_PRD-TUTORES v1.0`).

Expõe a API de administração de tutores e o runtime de conversação orquestrado por agente
(**Pydantic AI**), consumido por um widget incorporável via `<iframe>`.

> **Frontend companheiro:** [`dot-tutors-frontend`](https://github.com/JustShinobi/dot-tutors-frontend)

---

## Sumário

- [Arquitetura](#arquitetura)
- [Como rodar localmente](#como-rodar-localmente)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Fluxo de embed ponta a ponta](#fluxo-de-embed-ponta-a-ponta)
- [Decisões de arquitetura](#decisões-de-arquitetura)
- [Estratégia de conhecimento agêntica](#estratégia-de-conhecimento-agêntica)
- [Segurança do modelo de embed](#segurança-do-modelo-de-embed)
- [Testes e qualidade](#testes-e-qualidade)
- [Limitações conhecidas do MVP](#limitações-conhecidas-do-mvp)
- [Próximos passos para produção](#próximos-passos-para-produção)
- [Uso de agentes de codificação](#uso-de-agentes-de-codificação)

---

## Arquitetura

Ver [`docs/architecture.md`](docs/architecture.md) para o diagrama completo e o fluxo de sequência.

---

## Como rodar localmente

Pré-requisitos: **Python 3.12+**. Docker é opcional (só para rodar PostgreSQL).

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  no Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # ajuste GEMINI_API_KEY
alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --reload
```

A API sobe em <http://localhost:8000>; documentação interativa em `/docs`.

Com `uv` (opcional, mais rápido): `uv sync && uv run uvicorn app.main:app --reload`.

### PostgreSQL (opcional)

Por padrão o projeto usa **SQLite**, para subir sem nenhuma dependência externa. Para usar
PostgreSQL:

```bash
docker compose up -d db
# em .env:
# DATABASE_URL=postgresql+asyncpg://tutors:tutors@localhost:5432/tutors
alembic upgrade head
```

---

## Variáveis de ambiente

Ver [`.env.example`](.env.example) — nenhum segredo real é versionado.

---

## Fluxo de embed ponta a ponta

Documentado em [`docs/embed.md`](docs/embed.md).

---

## Decisões de arquitetura

| # | Decisão | Escolha |
|---|---|---|
| D1 | Framework de agente (PRD §4.3.1) | **Pydantic AI** (+ runner LangGraph alternativo para comparação) |
| D2 | Transporte do chat | **HTTP + SSE** (`text/event-stream`) |
| D3 | Banco de dados | **SQLite** por padrão, **PostgreSQL** suportado via `DATABASE_URL` |
| D4 | Auth do admin | **JWT HS256** com usuário seed |
| D5 | Auth do embed | **Embed key pública (`pk_`) + allowlist de `Origin` + session token curto** |
| D6 | Estratégia de conhecimento | **Tools agênticas + busca lexical BM25** — sem embeddings, sem vector DB |
| D7 | LLM | **Google Gemini** (`gemini-2.5-flash`), provider/modelo por env |
| D8 | Frontend | Next.js 15 App Router (repo separado) |
| D9 | Rate limit | Token bucket em memória (Redis documentado como próximo passo) |
| D10 | Repositórios | Dois repositórios Git distintos, Conventional Commits |

> As justificativas completas de cada decisão são escritas ao longo da implementação e consolidadas
> aqui na entrega final. A justificativa de **D1 (Pydantic AI vs LangChain)** é a mais detalhada, por
> ser o eixo central do desafio.

---

## Estratégia de conhecimento agêntica

_A ser detalhado na entrega (fase F4)._ Resumo: nenhuma dependência de banco vetorial, índice
vetorial ou modelo de embedding. O agente decide o que consultar através de ferramentas
(`list_sources`, `get_source_outline`, `search_source`, `fetch_source`) sobre fontes HTTP
configuradas no tutor, com busca **lexical (BM25)** dentro do documento em cache.

---

## Segurança do modelo de embed

_A ser detalhado na entrega (fase F3/F8)._

---

## Testes e qualidade

```bash
pytest              # testes
ruff check .        # lint
ruff format --check .
mypy app            # tipos
```

---

## Limitações conhecidas do MVP

_A ser consolidado na entrega final._

---

## Próximos passos para produção

_A ser consolidado na entrega final._

---

## Uso de agentes de codificação

Conforme a restrição de processo do PRD (§2) e o critério de aceite §7.6, **este código foi produzido
com auxílio de agentes de codificação**, e não por codificação integralmente manual.

- Ferramenta utilizada: **Claude Code (Anthropic)**.
- Papel humano: definição da arquitetura e das decisões técnicas, revisão crítica de cada saída,
  correções e validação por testes automatizados.
- O registro das iterações relevantes — incluindo casos em que a saída do agente foi rejeitada ou
  refeita — está em [`docs/agent-log.md`](docs/agent-log.md).
- As diretrizes fornecidas aos agentes estão em [`AGENTS.md`](AGENTS.md).
