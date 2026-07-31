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
- [Deploy com Docker](#deploy-com-docker)
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

```
                        ┌───────────────────────────────────────────────┐
                        │      SITE DO INTEGRADOR (terceiro)            │
                        │   <iframe src=".../embed/pk_live_abc123">     │
                        └───────────────────┬───────────────────────────┘
                                            │ (1) carrega iframe
                                            ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  FRONTEND — Next.js 16 (App Router, TS, Tailwind)      repo: *-frontend       │
│                                                                              │
│  /login  /tutors  /tutors/[id]  /tutors/[id]/embed            ← ADMIN        │
│  /embed/[embedKey]                                          ← WIDGET (iframe)│
│  /demo                                        ← página host de demonstração  │
│                                                                              │
│  middleware.ts → CSP frame-ancestors por tutor (consulta /embed/config)      │
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
│        │      │  instructions = configuração do tutor         │              │
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
│  │ tutors        │   │ (SSRF guard,       │    │ gemini-3.6-flash   │      │
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

Detalhes e diagrama de sequência em [`docs/architecture.md`](docs/architecture.md).

---

## Como rodar localmente

Pré-requisitos: **Python 3.12+**. Docker é opcional (só para PostgreSQL).

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # ajuste GEMINI_API_KEY
alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --reload
```

API em <http://localhost:8000>, documentação interativa em `/docs`.

O seed cria o administrador, dois tutores de demonstração — um com fontes HTTP públicas e estáveis
(PEP 20 e PEP 8) e outro com uma política interna fictícia em texto — **e uma chave de embed**,
imprimindo o link pronto de demonstração. É o caminho mais curto entre `git clone` e um widget
respondendo dentro de um iframe.

Com `uv` (opcional): `uv sync && uv run uvicorn app.main:app --reload`.

### PostgreSQL (opcional)

O padrão é **SQLite**, para subir sem dependência externa. Para usar PostgreSQL:

```bash
docker compose up -d db
# em .env: DATABASE_URL=postgresql+asyncpg://tutors:tutors@localhost:5432/tutors
alembic upgrade head
```

### Retenção

`python -m scripts.cleanup` apaga sessões expiradas e suas mensagens. Pensado para uma entrada
de cron; não há agendador no processo de propósito, porque uma thread de fundo dentro da API
rodaria uma vez por réplica.

(Os buckets de rate limit são a exceção: vivem *dentro* de um processo, então são limpos por uma
tarefa de fundo — por processo é exatamente o escopo certo para eles.)

---

## Deploy com Docker

A pilha completa — PostgreSQL, API e frontend — sobe com um comando. Pensada para uma VM ou LXC
atrás de um proxy reverso que termina TLS; os dois serviços escutam apenas em `127.0.0.1`, quem
publica na rede é o proxy.

```bash
cp .env.deploy.example .env.deploy    # ajuste URLs públicas, segredos e GEMINI_API_KEY
docker compose -f docker-compose.deploy.yml --env-file .env.deploy up -d --build
docker compose -f docker-compose.deploy.yml --env-file .env.deploy logs api | grep "Chave de embed"
```

O container aplica as migrations e roda o seed no boot (ambos idempotentes), e o seed **imprime a
chave de embed e o link pronto de demonstração** — não é preciso criar nada pelo painel para ver o
widget funcionando.

Três pontos que costumam morder num deploy assim:

| Ponto | O que fazer |
|---|---|
| **`TRUSTED_PROXY_HOPS`** | `1` com um proxy na frente. Se ficar em `0`, todos os visitantes compartilham o IP do proxy e o primeiro esgota o rate limit de todo mundo |
| **`PUBLIC_API_URL`** | Entra no bundle do frontend em **tempo de build**: mudar exige `--build`, não basta reiniciar |
| **Buffering do proxy** | O SSE precisa passar sem buffer. No nginx: `proxy_buffering off;` na rota da API (o backend já envia `X-Accel-Buffering: no`) |

Exemplo mínimo de nginx para a rota da API:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;          # sem isto o streaming chega todo de uma vez
    proxy_read_timeout 120s;      # maior que AGENT_TIMEOUT_SECONDS
}
```

`Dockerfile` e `docker-compose.deploy.yml` são construídos e testados no CI a cada push: a imagem
precisa subir e responder `/healthz` para o build passar.

---

## Variáveis de ambiente

Todas documentadas em [`.env.example`](.env.example), que não contém nenhum segredo real. As que
mais importam:

| Variável | Padrão | Para que serve |
|---|---|---|
| `DATABASE_URL` | SQLite local | Troque para `postgresql+asyncpg://...` para usar Postgres |
| `GEMINI_API_KEY` | vazio | Sem ela a API sobe, mas o chat responde erro claro |
| `LLM_MODEL` | `gemini-3.6-flash` | Modelo usado pelo agente |
| `AGENT_RUNNER` | `pydantic_ai` | `langgraph` ativa a implementação comparativa |
| `AGENT_MAX_TOOL_CALLS` | `6` | Teto de ferramentas por resposta |
| `AGENT_TIMEOUT_SECONDS` | `45` | Timeout total de uma execução |
| `HISTORY_MAX_MESSAGES` | `20` | Mensagens reproduzidas no contexto |
| `ADMIN_ORIGIN` | `http://localhost:3000` | Origem autorizada na API administrativa |
| `EMBED_DEFAULT_ORIGINS` | `http://localhost:3000` | Allowlist padrão ao criar uma chave |
| `TRUSTED_PROXY_HOPS` | `0` | Quantos proxies reversos existem na frente. `1` atrás de nginx/Traefik/Caddy |
| `EXPOSE_API_DOCS` | vazio | Vazio = `/docs` só fora de staging/production. `true` publica mesmo assim |

Os placeholders de `JWT_SECRET` e `SEED_ADMIN_PASSWORD` fazem a aplicação **recusar subir** com
`APP_ENV=staging` ou `production` — para o valor de exemplo nunca chegar a um ambiente real.

---

## Fluxo de embed ponta a ponta

Do ponto de vista do integrador: [`docs/embed.md`](docs/embed.md).

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

---

## Decisões de arquitetura

| # | Decisão | Escolha | Por quê |
|---|---|---|---|
| D1 | Framework de agente (§4.3.1) | **Pydantic AI** | Ver seção dedicada abaixo |
| D2 | Transporte do chat (§3.3) | **HTTP + SSE** | A conversa é requisição/resposta com saída em stream. Bidirecionalidade não compraria nada e custaria estado de conexão, um segundo caminho de autenticação e testes mais difíceis. SSE transmite token a token, atravessa proxies e reconecta trivialmente. Endpoint irmão com `stream=false` para testes e chamadas server-to-server. |
| D3 | Banco de dados (§4.4.1) | **SQLite** por padrão, **PostgreSQL** por env | SQLite deixa o projeto subir com um comando — o que importa para quem vai avaliar. O schema é agnóstico de dialeto de propósito: sem `JSONB`, sem enum nativo, timestamps normalizados em UTC por um `TypeDecorator`, e foreign keys explicitamente habilitadas no SQLite (que as ignora por padrão). Os testes rodam em SQLite justamente para essa paridade não ser só uma afirmação. |
| D4 | Auth do admin (§4.1.2) | **JWT HS256** | Permite expiração e claims de papel sem tabela de sessão. |
| D5 | Auth do embed (§3.4) | **Chave pública + allowlist de origem + token de sessão curto** | Ver [Segurança do modelo de embed](#segurança-do-modelo-de-embed) |
| D6 | Estratégia de conhecimento (§4.3.2) | **Tools agênticas + BM25 lexical** | Ver seção dedicada |
| D7 | LLM | **Google Gemini** (`gemini-3.6-flash`) | Provider e modelo por env. |
| D8 | Rate limit (§5.1) | Token bucket em memória | Simples e suficiente para demo; a limitação está declarada, não escondida. |
| D9 | CORS (§5.1) | Política dupla em middleware próprio | Ver seção de segurança |

### D1 — Por que Pydantic AI, e não LangChain

A pergunta não é qual framework é mais completo, e sim **qual é o menor conjunto de abstrações
que resolve este problema sem impedir o próximo**.

**1. O PRD remove justamente o diferencial do LangChain.** O valor dele se concentra em
integrações de RAG — loaders, retrievers, vector stores — e o §6.2 veta exatamente isso. Sobra o
LangGraph, cujo ganho aparece em grafos com múltiplos nós, ramificação e checkpointing. Aqui há
**um agente e um loop de ferramentas**.

**2. Testabilidade decidiu.** O §5.3 exige testes nos pontos críticos, e o ponto crítico é o
agente. `FunctionModel` permite rodar o **loop real, as ferramentas reais e a recuperação BM25
real** com o modelo roteirizado — sem chave de API, sem rede, sem custo de token e sem
instabilidade. São 18 testes que provam que o agente busca na fonte certa, se recupera de um id
alucinado e degrada quando uma fonte cai.

**3. Tipagem e injeção de dependências combinam com a stack.** `RunContext[AgentDeps]` é o
`Depends` do FastAPI dentro do agente. `mypy --strict` passa de ponta a ponta.

**O que se perde:** o catálogo de integrações (irrelevante aqui — as fontes são HTTP simples) e
o LangGraph para fluxos com estado (nenhum hoje).

**Como a decisão foi protegida.** O agente fica atrás do protocolo `AgentRunner`. **Nenhum módulo
de `app/` fora de `app/agent/` importa `pydantic_ai`** — os testes importam, porque é de lá que vem
o `FunctionModel`. E há um segundo runner, em LangGraph, atrás da mesma
interface — instalável com `pip install -e ".[langgraph]"` e ativável com `AGENT_RUNNER=langgraph`.
O que a comparação mostrou:

- **A camada de conhecimento não se moveu.** `SourceService` e `app/agent/tools.py` ficaram
  intactos; só a fiação mudou. É a prova de que as ferramentas foram construídas como lógica de
  domínio, não como artefato de framework.
- **O streaming é onde está o custo.** `run_stream_events` entrega deltas de texto e eventos de
  ferramenta num único iterador tipado. O equivalente exige `astream_events`, filtro por nome de
  evento e mapeamento manual de formatos de chunk — tipado por string, com falha só em runtime.
- **O teste é a diferença real.** Roteirizar chamadas de ferramenta custa poucas linhas com
  `FunctionModel`; o equivalente exige um chat model falso implementando a interface do LangChain.

Nada disso faz do LangGraph uma ferramenta ruim — ele é forte para grafos com estado. É apenas
mais maquinaria do que um único loop de tool-calling precisa.

---

## Estratégia de conhecimento agêntica

**Não há banco vetorial, índice vetorial nem modelo de embedding neste projeto.** Isso não é uma
afirmação de README: `scripts/check_no_vector_deps.py` falha o build se uma dependência banida
entrar no `pyproject.toml` ou se um símbolo de embedding for importado por `app/` — inclusive a
API de embeddings que o próprio `pydantic-ai` oferece. O script roda no pre-commit e no CI.

O agente decide o que consultar, uma chamada por vez:

| Ferramenta | Papel |
|---|---|
| `list_sources()` | Catálogo do que existe |
| `get_source_outline(source_id)` | Títulos das seções — permite **navegar** antes de ler |
| `search_source(source_id, query)` | Busca por palavras-chave (BM25) dentro de uma fonte |
| `fetch_source(source_id, offset)` | Leitura sequencial paginada |

O catálogo de fontes é injetado nas instruções, poupando uma chamada de `list_sources` a cada
mensagem.

**Sobre o BM25.** A relevância é decidida por **sobreposição de tokens**; o BM25 apenas ordena os
candidatos. O motivo é concreto: o IDF do BM25 fica negativo quando o termo aparece na maior
parte do corpus, e num documento curto — um FAQ, uma política enxuta — esse é o caso normal. Um
filtro por `score > 0` tornava documentos pequenos silenciosamente impesquisáveis. Está fixado por
teste de regressão.

**Limites.** Teto de chamadas de ferramenta (`UsageLimits.tool_calls_limit`), timeout total da
execução, truncamento por ferramenta e por documento. Uma fonte fora do ar devolve erro
estruturado ao agente em vez de derrubar a conversa — ele avisa e responde com as demais.

---

## Segurança do modelo de embed

A chave `pk_live_...` viaja no `src` do iframe e **é pública por natureza**. Tratá-la como
segredo seria autoengano. O que protege o tutor:

1. **Allowlist de origens por chave**, conferida contra o header `Origin` a cada abertura de
   sessão. A comparação é exata — sem `startswith`, sem `endswith`, sem substring, que são as
   formas clássicas de furar uma allowlist. Testes cobrem sufixo enganoso
   (`cliente.com.attacker.net`), subdomínio não listado, esquema e porta diferentes, `Origin: null`
   de iframe sandbox e `Origin` ausente.
2. **`frame-ancestors` por tutor**, emitido pelo frontend. Complementa, não duplica: esse header
   impede a página de **renderizar** em site hostil (garantia do navegador); a checagem de
   `Origin` impede a sessão de **abrir** (garantia do servidor).
3. **Token de sessão curto e escopado**, com claim `aud` separando-o do token de admin — um token
   do widget não pode ser reaproveitado na API administrativa, e há teste para isso.
4. **Rate limit** por sessão e por IP.
5. **O segredo real — a chave do LLM — nunca sai do backend.**

**Sobre CORS.** A API de embed **ecoa qualquer origem**, e isso é deliberado. CORS é uma proteção
de *leitura* do navegador, não um mecanismo de autorização: uma resposta bloqueada já chegou ao
servidor e já executou. Recusar origens desconhecidas nessa camada não protegeria nada e quebraria
todo integrador legítimo. A autorização fica onde deve estar — no `Origin` conferido contra a
allowlist, que responde `403` antes de qualquer trabalho. Credenciais ficam desligadas, porque o
token viaja no header `Authorization` e nunca em cookie.

**Revogação tem efeito imediato.** Um token de sessão vale por todo o seu TTL, então revogar uma
chave só significaria alguma coisa se a sessão fosse reconferida. Ela é: cada mensagem revalida a
chave e a origem gravada contra a allowlist atual. Tirar um domínio da lista derruba as conversas
em andamento daquele domínio, sem esperar 30 minutos.

**Força bruta no login.** `POST /auth/login` é o único endpoint que verifica senha e tem limite
por IP. A checagem vem **antes** da verificação — bcrypt é lento de propósito, o que torna
tentativas ilimitadas também um vetor de exaustão de CPU.

**Atrás de proxy reverso.** `TRUSTED_PROXY_HOPS` diz quantos proxies existem na frente. Com `0`
(padrão) o `X-Forwarded-For` é ignorado, porque é um header que o cliente controla e confiar nele
sem proxy daria um bucket de rate limit novo a cada requisição. Com `1`, o endereço é lido a uma
posição da direita da cadeia — as entradas da esquerda são forjáveis, as da direita foram
acrescentadas pela sua infraestrutura.

**Outras defesas.** Fetcher com cerca anti-SSRF: só http/https, IPs públicos, revalidado a cada
redirecionamento, teto de bytes aplicado durante o streaming — e **conexão fixada no endereço que
acabou de ser validado**, com o hostname preservado no `Host` e no SNI. Sem essa fixação, checar e
conectar são duas resoluções de DNS diferentes, e um servidor hostil com TTL de um segundo
responde a primeira com um IP público e a segunda com `169.254.169.254`. Além disso: respostas de
erro sem stack trace com `request_id` correlacionável, limite de tamanho de requisição, headers de
resposta (`nosniff`, `no-referrer`, `X-Frame-Options: DENY` na própria API) e `/docs` desligado
fora de desenvolvimento (`EXPOSE_API_DOCS` religa quando a demonstração pede).

---

## Testes e qualidade

```bash
pytest              # 280 testes, cobertura ~90%
ruff check .        # lint
ruff format --check .
mypy app scripts    # tipagem estrita
python scripts/check_no_vector_deps.py
```

Tudo isso roda no CI a cada push e pull request, em três jobs:

1. **Lint, tipos e testes** em SQLite, com piso de cobertura e `alembic check` para pegar um
   modelo alterado sem migração.
2. **PostgreSQL**: as migrations sobem e descem, e o seed roda contra o dialeto real. Sem este
   job, "o schema é agnóstico de dialeto" seria uma afirmação de README que nada verifica.
3. **Imagem de contêiner**: a imagem é construída e precisa responder `/healthz` — o arquivo que
   vai a produção não pode ser o único que ninguém executa.

Os testes rodam contra um **SQLite real em memória** — constraints, unicidade e cascade são
exercitados de verdade, sem mock de ORM e sem servidor externo. Os testes do agente exercitam o
loop real com o modelo substituído.

---

## Limitações conhecidas do MVP

Declaradas, não escondidas:

- **Rate limit em memória.** Não sobrevive a restart nem é compartilhado entre réplicas: um
  deploy horizontal multiplicaria o limite pelo número de instâncias. Os buckets ociosos são
  descartados periodicamente para o dicionário não crescer sem limite.
- **Prompt injection mitigada, não resolvida.** Conteúdo de fonte vai delimitado por `<fonte>` com
  instrução explícita de tratá-lo como dado. Um documento hostil ainda é uma superfície de risco.
- **Sem multi-tenant.** Um único papel administrativo, sem isolamento entre organizações (§6.3).
- **Histórico limitado a N mensagens**, sem sumarização do que fica fora da janela.
- **Cache de fonte por TTL**, sem invalidação por webhook — mas há um endpoint de reprocessamento
  manual (`POST /tutors/{id}/sources/{sid}/refresh`) para não depender de esperar o TTL.
- **Sessão do admin sem refresh token.** Expirado o access token, é preciso logar de novo.
- **Migrations rodam no boot do container.** Correto para uma instância; com réplicas isso vira
  corrida e o certo é um job separado no pipeline. É por isso que o passo é uma flag
  (`RUN_MIGRATIONS_ON_START`), não um comportamento fixo.
- **Um único provider de LLM.** `LLM_PROVIDER` só aceita `google`; trocar exige um caso a mais em
  `build_model`.

---

## Próximos passos para produção

**Segurança e multi-tenant**
- Organizações e RBAC, com `org_id` em todas as queries.
- API keys server-to-server (`sk_`) com hash em repouso, escopos e rotação; log de auditoria.
- Rotação e expiração automática de embed keys.
- Secret manager no lugar de `.env`.
- Refresh token para o painel administrativo.

**Escala e confiabilidade**
- Rate limit e cache de fontes em Redis (hoje em memória, por processo).
- Ingestão de fontes assíncrona, com agendamento e invalidação por webhook.
- Circuit breaker nas chamadas de LLM e de fetch (o retry com backoff já existe).
- Particionamento de `chat_messages` por data; réplicas de leitura.
- Migrations como job separado do boot, para suportar múltiplas réplicas.

**Produto**
- Versionamento de tutores com preview e rollback.
- Playground de teste no admin antes de publicar.
- Temas e i18n do widget; SDK JS e Web Component além do iframe; modo bolha flutuante.
- Analytics: perguntas sem resposta na base, tópicos frequentes, 👍/👎 por mensagem.
- Handoff para humano; upload de arquivos como fonte.

**Qualidade de IA**
- Suíte de *evals* com casos de referência (fidelidade à fonte, recusa correta, aderência à
  persona) rodando no CI.
- Guardrails de moderação e detecção de prompt injection em conteúdo de fonte.
- Tracing com OpenTelemetry/Logfire, replay de execuções e custo por conversa.
- Roteamento de modelo por complexidade e cache semântico.

**Compliance**
- LGPD: retenção configurável, anonimização, exportação e exclusão por sessão.
- Aviso de IA no widget e registro de base legal por integrador.
- Auditoria de acessibilidade WCAG 2.1 AA.

---

## Uso de agentes de codificação

Conforme a restrição de processo do PRD (§2) e o critério de aceite §7.6, **este código foi
produzido com auxílio de agentes de codificação**, e não por codificação integralmente manual.

- **Ferramenta:** Claude Code (Anthropic).
- **Papel humano:** definição da arquitetura e das decisões técnicas, revisão crítica de cada
  saída, correções e validação por testes automatizados e execução real.
- **Registro das iterações:** [`docs/agent-log.md`](docs/agent-log.md) — inclui os casos em que a
  saída do agente foi **rejeitada ou corrigida**, que é o que o desafio pede para avaliar. Entre
  eles: a rejeição do LangChain como framework principal, o bug do BM25 que tornava documentos
  curtos impesquisáveis, o primeiro token descartado no streaming, e o `admin@dot.local` que
  impedia o login do administrador do seed.
- **Diretrizes fornecidas aos agentes:** [`AGENTS.md`](AGENTS.md).
