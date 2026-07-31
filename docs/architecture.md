# Arquitetura

Material de apoio exigido pelo PRD §8.1.

## Visão geral

```
                        ┌───────────────────────────────────────────────┐
                        │      SITE DO INTEGRADOR (terceiro)            │
                        │   <iframe src=".../embed/pk_live_abc123">     │
                        └───────────────────┬───────────────────────────┘
                                            │ (1) carrega iframe
                                            ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  FRONTEND — Next.js 15 (App Router, TS, Tailwind)      repo: *-frontend       │
│                                                                              │
│  /login  /tutors  /tutors/new  /tutors/[id]  /tutors/[id]/embed   ← ADMIN    │
│  /embed/[embedKey]                                          ← WIDGET (iframe)│
│  /demo                                        ← página host de demonstração  │
│                                                                              │
│  middleware.ts → CSP frame-ancestors por tutor (consulta o backend)          │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ (2) POST /embed/session {embed_key} + Origin
                                │ (3) POST /embed/chat  (SSE)  Bearer <session_token>
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  BACKEND — FastAPI (Python 3.12)                        repo: *-backend      │
│                                                                              │
│  ┌────────────┐  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │ API Admin  │  │  API Embed   │  │  Middlewares  │  │  Observabilidade │  │
│  │ JWT admin  │  │ pk + origin  │  │ CORS · rate   │  │ structlog JSON   │  │
│  │ CRUD tutor │  │ session tok  │  │ limit · errs  │  │ request_id       │  │
│  └─────┬──────┘  └──────┬───────┘  └───────────────┘  └──────────────────┘  │
│        │                │                                                    │
│        │                ▼                                                    │
│        │      ┌──────────────────────────────────────────────┐              │
│        │      │  AGENTE  (Pydantic AI · porta AgentRunner)    │              │
│        │      │  instructions = instruções do tutor           │              │
│        │      │  ┌────────────────────────────────────────┐  │              │
│        │      │  │ TOOLS (estratégia agêntica, sem vetor) │  │              │
│        │      │  │  • list_sources()                      │  │              │
│        │      │  │  • get_source_outline(source_id)       │  │              │
│        │      │  │  • search_source(source_id, query) BM25│  │              │
│        │      │  │  • fetch_source(source_id, offset)     │  │              │
│        │      │  └───────────────┬────────────────────────┘  │              │
│        │      └──────────────────┼───────────────────────────┘              │
│        │                         │                                          │
│        ▼                         ▼                        ▼                 │
│  ┌───────────────┐   ┌────────────────────┐    ┌────────────────────┐      │
│  │ SQLite / PG   │   │ Fetcher HTTP       │    │ Google Gemini API  │      │
│  │ tutors        │   │ (SSRF guard,       │    │ gemini-2.5-flash   │      │
│  │ sources+cache │   │  timeout, maxbytes)│    └────────────────────┘      │
│  │ embed_keys    │   └─────────┬──────────┘                                │
│  │ sessions      │             │                                            │
│  │ messages      │             ▼                                            │
│  └───────────────┘   ┌──────────────────────┐                              │
│                      │ Fontes HTTP públicas │                              │
│                      │ (.md / .txt / .json) │                              │
│                      └──────────────────────┘                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Fluxo de embed ponta a ponta

```
Integrador   Widget(iframe)      Backend                Agente          Fonte/LLM
    │              │                │                     │                │
    │──renderiza──►│                │                     │                │
    │              │─POST /embed/session {pk} ───────────►│                │
    │              │   (valida pk + Origin + tutor ativo) │                │
    │              │◄── {session_token, tutor, history} ──│                │
    │              │                │                     │                │
    │              │─POST /embed/chat (SSE) Bearer tok ──►│                │
    │              │                │─ carrega últimas N msgs ─►           │
    │              │                │─ run(agent, msg, deps) ──────────────►│
    │              │                │                     │─ tool: search ►│
    │              │                │                     │◄── trechos ────│
    │              │                │                     │─ LLM ─────────►│
    │              │◄══ event: token ══ (stream) ═════════│◄──────────────-│
    │              │◄══ event: done {message_id, sources} │                │
    │              │  persiste user+assistant msgs        │                │
```

## Camadas do backend

| Camada | Pasta | Responsabilidade |
|---|---|---|
| HTTP | `app/api/` | Rotas, validação de entrada, autenticação, serialização. Não acessa o ORM. |
| Domínio | `app/services/` | Regra de negócio. Não conhece objetos de request/response. |
| Dados | `app/repositories/` + `app/db/` | Persistência e queries. |
| Agente | `app/agent/` | Orquestração do LLM e ferramentas de conhecimento. Isolado atrás de `AgentRunner`. |
| Infra | `app/core/` + `app/utils/` | Config, logging, erros, segurança, cliente HTTP. |

Nenhum módulo fora de `app/agent/` importa `pydantic_ai` — é o que torna a decisão D1 reversível
e permite o runner alternativo em LangGraph.
