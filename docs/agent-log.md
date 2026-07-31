# Registro de iterações com agentes de codificação

Exigência de processo do PRD (§2) e critério de aceite §7.6. Este arquivo registra as iterações
relevantes do desenvolvimento assistido por agente — **incluindo os casos em que a saída do agente
foi rejeitada ou corrigida**, que é o que o desafio pede para avaliar (prompting, iteração, revisão
e validação).

Ferramenta: **Claude Code (Anthropic)**.

---

## #1 — Escolha do framework de agente: LangChain vs Pydantic AI

**Contexto.** O PRD §4.3.1 permite as duas famílias, exigindo justificativa explícita. A inclinação
inicial era LangChain, por ser "mais completo".

**Revisão humana.** A avaliação apontou que o PRD §6.2 **remove justamente a camada onde o LangChain
concentra valor** (retrievers, vector stores, loaders de RAG). O que sobra do LangChain para este
escopo é LangGraph, cujo ganho aparece em grafos com múltiplos nós e estado — e aqui há um único
agente com loop de tools.

**Decisão.** Pydantic AI como implementação principal, com três argumentos objetivos: (a) o
diferencial do LangChain está fora de escopo; (b) `TestModel`/`FunctionModel` permitem cumprir o
requisito de testes do §5.3 **sem chamar o LLM**; (c) `RunContext[Deps]` espelha o `Depends` do
FastAPI, mantendo a stack coerente e `mypy --strict` viável.

**Mitigação do risco.** O agente foi isolado atrás do protocolo `AgentRunner`, e um runner
alternativo em LangGraph está planejado para a fase final — a decisão passa a ser comparativa e
reversível, não uma aposta.

---

## #2 — Versão real do `pydantic-ai` vs. conhecimento do modelo

**Problema.** O plano foi escrito assumindo a API do `pydantic-ai` 0.x (`agent.run_stream` +
inspeção manual de partes da resposta para detectar chamadas de ferramenta).

**Validação.** Antes de escrever qualquer código do agente, a API instalada foi inspecionada por
introspecção (`inspect.signature`) em vez de confiar na memória do modelo. A versão resolvida é a
**2.21.0**, com diferenças relevantes:

- existe `Agent.run_stream_events(...)`, que entrega **num único stream** os deltas de texto
  (`PartDeltaEvent`) e os eventos de ferramenta (`FunctionToolCallEvent`,
  `FunctionToolResultEvent`) — exatamente o que a UI do widget precisa para mostrar "consultando
  a fonte X";
- `UsageLimits` expõe `tool_calls_limit`, que implementa o guardrail `AGENT_MAX_TOOL_CALLS` sem
  código próprio;
- `Agent(instructions=...)` substitui o antigo `system_prompt` como forma recomendada.

**Correção.** O desenho do runner foi ajustado para `run_stream_events` antes da implementação,
evitando escrever e depois refatorar a camada de streaming.

**Lição aplicada ao restante do projeto.** Toda API de biblioteca cuja versão for mais recente que
o conhecimento do modelo é verificada por introspecção antes do uso.

---

## #3 — Ambiente de desenvolvimento bloqueado por política do Windows

**Problema.** O `uv` e o `mypy` (binários compilados) foram bloqueados pelo Smart App Control da
máquina de desenvolvimento, quebrando o gate de qualidade.

**Iteração.** Primeiro caminho: reinstalar o `mypy` a partir do sdist (`--no-binary`), obtendo uma
build pura em Python que roda sob a política. Solução válida, porém mais lenta.

**Resolução.** A política foi ajustada no ambiente e as ferramentas compiladas voltaram a funcionar.
Ficou registrado no README que o projeto **não depende do `uv`**: `pip install -e ".[dev]"` é
suficiente, o que evita transferir esse atrito para quem for avaliar.

---

## #4 — Guarda automatizada contra RAG vetorial

**Contexto.** O critério §7.4 exige que nenhuma dependência de banco vetorial ou embeddings seja o
núcleo da estratégia de conhecimento. Uma afirmação no README não é verificável.

**Decisão.** Foi criado `scripts/check_no_vector_deps.py`, que falha o build se (a) uma dependência
banida aparecer no `pyproject.toml` ou (b) um símbolo de embedding for importado por
`app/` — inclusive a API de embeddings que o próprio `pydantic-ai` oferece e que **não** é usada
aqui. O script roda no pre-commit e no CI, transformando o critério de aceite em teste executável.

---

## #5 — O admin do seed não conseguia fazer login

**Problema.** O placeholder `admin@dot.local` foi usado no `.env.example`, no default do `Settings`
e nos testes. A rota de login valida o e-mail com `EmailStr`, e o `email-validator` **recusa
domínios de uso especial** (`.local`, `.localhost`, `.invalid`, RFC 6762/2606). Resultado: o
administrador criado pelo seed recebia `422` em toda tentativa de login — o demo inteiro ficaria
inacessível.

**Como apareceu.** Não por leitura do código: os testes de API quebraram em massa, todos com `422`
onde se esperava `200`, e a fixture de token derrubou 18 testes por arrasto.

**Correção.** Todos os placeholders passaram para `admin@example.com` (`example.com` é reservado
pela RFC 2606 exatamente para documentação e passa na validação), e o `.env.example` ganhou um
comentário explicando por que um domínio reservado não serve — para o próximo leitor não repetir.

**Lição.** Um placeholder plausível não é um placeholder válido. Vale rodar o caminho real (migrar,
seed, subir a API e autenticar de verdade) e não só a suíte de testes: foi o que confirmou o
fluxo completo depois da correção.

---

## #6 — BM25 tornava documentos curtos silenciosamente impesquisáveis

**Problema.** A busca lexical filtrava resultados por `score > 0`, o que parece óbvio e está
errado. O termo IDF do BM25 fica **negativo** quando a palavra aparece na maior parte do corpus —
e num documento curto, que vira um ou dois chunks, esse é o caso normal. Consequência: um FAQ
pequeno ou uma política enxuta retornavam **zero trechos**, e o tutor responderia "não encontrei
essa informação" com a resposta bem na frente. Nenhuma exceção, nenhum log de erro.

**Como apareceu.** Um teste de segurança (conteúdo hostil delimitado como dado) falhou por um
motivo completamente diferente do que investigava: a busca por "instrucoes" num texto que continha
a palavra devolveu nada. Vale registrar que o teste que pegou o bug não era o teste do bug.

**Correção.** A relevância passou a ser decidida por **sobreposição de tokens**, e o BM25 ficou
responsável apenas por **ordenar** os candidatos. Isso separa duas perguntas que estavam
indevidamente unidas: "este trecho é candidato?" (contém algum termo da busca) e "qual vem
primeiro?" (score). Dois testes de regressão fixam o comportamento — um documento de chunk único
é pesquisável, e continua rejeitando uma busca não relacionada.

---

## #7 — A primeira palavra de toda resposta era descartada

**Problema.** O tradutor de eventos do streaming tratava apenas `PartDeltaEvent`. No Pydantic AI, o
primeiro pedaço de texto chega num `PartStartEvent` e só as continuações vêm como delta. Toda
resposta do tutor sairia sem a palavra inicial.

**Como apareceu.** Pelo teste que monta a resposta em pedaços e compara a concatenação com o texto
esperado (`assert ' em partes.' == 'Resposta em partes.'`). Um fake que entregasse a resposta num
único pedaço teria passado e escondido o bug — por isso o modelo de teste emite palavra por
palavra de propósito.

**Lição aplicada.** Fakes devem imitar a *forma* do protocolo real, não só o resultado final. Foi o
mesmo princípio que exigiu trocar `FunctionModel(função)` por `FunctionModel(stream_function=...)`:
o caminho de streaming é outro código, e testá-lo pelo caminho não-streaming não prova nada.

---

## #8 — Toda requisição de chat quebrava no banco padrão

**Problema.** `TypeError: can't compare offset-naive and offset-aware datetimes` ao validar a
expiração da sessão. Causa: **o SQLite não tem tipo com fuso horário** e devolve o timestamp
naive, enquanto `utcnow()` é aware. No PostgreSQL, com `timestamptz`, o mesmo código funciona.

Ou seja: um bug que aparece **apenas na configuração padrão** — a que qualquer avaliador vai usar
primeiro — e que contradizia diretamente a decisão D3, que promete um schema agnóstico de dialeto.

**Correção.** Um `TypeDecorator` (`UtcDateTime`) normaliza na fronteira do banco: grava sempre em
UTC e devolve sempre aware, em qualquer dialeto. A alternativa — espalhar conversões defensivas
pelos serviços — trataria o sintoma em cada ponto de uso e deixaria o próximo timestamp exposto.
`alembic check` confirmou que o DDL gerado não mudou, então não houve migração nova.

**Lição.** "Roda em SQLite e em PostgreSQL" não é uma afirmação de configuração, é uma afirmação
que precisa de teste. Os testes rodam em SQLite justamente por isso.

---

## #9 — O `/openapi.json` inteiro quebrava por um `model` no lugar errado

**Problema.** Ao declarar que a rota de chat responde tanto `text/event-stream` quanto
`application/json`, o schema do Pydantic foi colocado dentro de `content`
(`{"application/json": {"model": ChatResponse}}`). O FastAPI não interpreta `model` nesse nível:
ele tentava serializar a *classe* e explodia com `PydanticSerializationError`, derrubando a
geração de todo o documento OpenAPI — inclusive o `/docs`.

**Como apareceu.** Pelo teste de fumaça `test_openapi_schema_is_served`, escrito na primeira fase
como algo quase trivial. Foi o único teste da suíte que pegou isso, e uma rota nova em outra área
do código foi o que o quebrou.

**Correção.** `model` sobe para o nível da resposta e `content` fica só com a mídia adicional.

**Lição.** Testes de fumaça baratos pagam por si mesmos em momentos que não se pode prever.

---

## #10 — Nenhuma foreign key era aplicada no banco padrão

**Problema.** O teste da rotina de retenção falhou por um motivo inesperado: apagadas as sessões
expiradas, **as mensagens continuavam lá**. Investigando: o SQLite **ignora foreign keys a menos
que sejam explicitamente habilitadas**, por conexão, e o padrão é desligado.

A consequência era muito maior que o teste: **todos os `ondelete="CASCADE"` do schema eram
decorativos** no banco local. Remover um tutor deixaria fontes, chaves, sessões e mensagens
órfãs — e a rotina de retenção, que existe para eliminar conteúdo de conversa, apagaria só a
casca. O PostgreSQL aplica as constraints nativamente, então o problema era invisível em qualquer
ambiente com paridade de produção, e sistemático em todos os outros.

**Correção.** Um listener de `connect` emite `PRAGMA foreign_keys=ON` para toda conexão SQLite.
É a mudança que faz os dois dialetos realmente se comportarem igual, em vez de apenas parecerem.

**Por que só apareceu agora.** Nenhum teste anterior exercitava exclusão em massa; os cascades
que passaram usavam o ORM, que emula o comportamento em Python e mascarava a ausência da
constraint no banco. Vale o registro: *o teste que passa pelo ORM não prova nada sobre o banco.*

---

## #11 — Duas correções que o teste de hardening extraiu

**`request_id` nulo justamente no 500.** O corpo de erro trazia `request_id: null` exatamente na
resposta em que ele mais importa. Causa: o Starlette executa o handler de último recurso no
`ServerErrorMiddleware`, **fora** da task onde o middleware de contexto define a contextvar. O
identificador aparecia no log e sumia da resposta — quebrando justamente a ponte entre o que o
usuário vê e o que está registrado. Passou a ser lido de `request.state`, que não depende de
contexto.

**Erro fora do alcance do handler.** O middleware de limite de payload levantava um `AppError`,
mas roda **fora** da pilha de exception handlers — o erro escapava como 500 genérico, com um
formato de corpo diferente de todo o resto da API. Agora ele constrói a resposta diretamente,
mantendo um único contrato de erro.

Ambos são a mesma lição em versões diferentes: **a ordem do middleware não é detalhe de
configuração**, ela decide o que o handler enxerga.

---

## #12 — O runner LangGraph, e o que a comparação de fato mostrou

**Contexto.** A decisão D1 (Pydantic AI) foi tomada na primeira iteração. Uma justificativa
escrita antes de existir código é uma opinião; a fase final implementou o **mesmo agente em
LangGraph**, atrás da mesma interface `AgentRunner`, para transformá-la em comparação.

**Resultado mais relevante — e não era o esperado.** O ganho não estava em nenhum dos dois
frameworks, e sim no fato de a camada de conhecimento **não ter se movido**: `SourceService` e
`app/agent/tools.py` ficaram intactos, só a fiação mudou. Isso valida retroativamente a decisão
de escrever as ferramentas como lógica de domínio em vez de artefato de framework — e é o que
tornou a troca uma tarde de trabalho em vez de uma reescrita.

**Onde o custo apareceu.** Streaming (um iterador tipado contra `astream_events` com mapeamento
por string, que falha só em runtime) e teste (poucas linhas com `FunctionModel` contra um chat
model falso implementando a interface do LangChain).

**Conclusão honesta registrada no README:** nada disso faz do LangGraph uma ferramenta ruim. Ele é
forte para grafos com estado. É apenas mais maquinaria do que um único loop de tool-calling
precisa — que é exatamente o que este PRD pede.

---

## #13 — O tratador de erro do SSE morria antes de emitir o erro

**Contexto.** Uma revisão do próprio código encontrou o bug mais sério do projeto, e ele estava
exatamente no bloco escrito para lidar com falhas — que é onde a suíte não olhava, porque os
runners engolem as próprias exceções e emitem `event: error` sozinhos. O caminho de último
recurso do transporte nunca era exercitado.

**O bug.** Em `_event_stream`:

```python
except Exception:
    await session.rollback()
    logger.exception("chat_stream_failed", session_id=chat_session.id)  # ← estoura aqui
```

`rollback()` expira todas as instâncias ORM. Ler `chat_session.id` logo depois dispara um
lazy-load síncrono dentro do contexto de streaming, e o SQLAlchemy levanta `MissingGreenlet`. O
`event: error` nunca era emitido: a resposta simplesmente abortava no meio.

**Como apareceu.** Reproduzindo o cenário "subiu sem `GEMINI_API_KEY`". A lifespan deixa
`agent_runner = None` de propósito, para o painel continuar funcionando — mas aí o serviço
chamava `.stream()` em `None`, e o `AttributeError` caía justamente naquele `except`.

**As duas correções.** Ler o `session_id` **antes** do `try` resolve o sintoma. A causa foi
tratada onde de fato estava: `get_agent_runner` agora recusa a requisição com `503
AGENT_UNAVAILABLE` — numa *dependência*, antes de a resposta começar —, então o cliente recebe um
erro de verdade em vez de um stream que abre e morre.

**A metade do cliente.** O parser de SSE do frontend saía do laço quando o stream fechava sem
`done` nem `error`, sem chamar handler nenhum: a bolha ficava "escrevendo…" para sempre. Agora um
stream sem evento terminal vira `STREAM_INCOMPLETE`.

**Lição registrada.** Um bloco `except` também é código, e testes que só exercitam o caminho feliz
do erro (aqui: a falha que o *runner* trata) não cobrem a falha que passa por fora dele. Três
testes novos garantem os dois transportes e o caso do modelo não configurado.

---

## #14 — Configuração administrativa que ninguém lia

**Contexto.** `model_settings` aceitava `model`, `temperature`, `max_tool_calls` e
`max_output_tokens`, validava os quatro, persistia os quatro — e o runner só lia
`max_tool_calls`. O comentário no modelo dizia "Overrides the global defaults".

**Decisão.** Implementar, não remover: são úteis e já estavam documentados como se funcionassem.
`ModelOverrides` traduz a coluna JSON para algo tipado, e os dois runners aplicam. Um modelo
injetado (os testes) ignora o override de modelo, para um tutor não conseguir apontar a suíte
para um provider real.

**Detalhe que só apareceu ao escrever o teste.** Em Python `True` é `int`, então
`max_output_tokens: true` viraria `1` silenciosamente. A conversão rejeita `bool` explicitamente,
e há teste para isso.

---

## #15 — `gather` sobre uma sessão do SQLAlchemy: quase um bug

**Contexto.** O catálogo de fontes é montado a cada turno, carregando todas as fontes em série.
Com cache frio isso custa um timeout **por fonte** antes do primeiro token, o que pode estourar o
timeout do agente sozinho.

**Primeira tentativa, errada.** `asyncio.gather(*(self.load(s) for s in sources))`. Escrevi
inclusive um comentário afirmando que as escritas seriam "serializadas pelo lock da sessão". Não
são: `AsyncSession` não é segura para uso concorrente e levanta `IllegalStateChangeError` em vez
de enfileirar.

**Correção.** `load_many` em três fases — planejar (banco, sequencial), buscar (rede, concorrente),
persistir (banco, sequencial). Só a fase do meio vai para o `gather`, e é onde estão os segundos.

**Lição.** O comentário estava mais errado que o código, e teria sobrevivido à revisão por
soar plausível. Concorrência é onde "parece certo" é a armadilha mais cara.

---

## #16 — A cerca anti-SSRF checava um nome e conectava em outro

**Contexto.** `assert_public_url` resolvia o host e validava os IPs; depois o httpx resolvia de
novo para conectar. Duas resoluções independentes — a janela clássica de DNS rebinding: um
servidor autoritativo hostil com TTL de um segundo responde a primeira com IP público e a segunda
com `169.254.169.254`.

**Correção.** A validação agora **devolve** os endereços, e a conexão é feita no endereço
validado, com o hostname original preservado no header `Host` e no `sni_hostname` — assim o
virtual hosting continua roteando e o certificado TLS continua sendo verificado contra o nome, não
contra um IP.

**Validação.** Antes de aceitar, testei contra as fontes reais do seed (as PEPs, inclusive num
redirecionamento `http → https`). Um "endurecimento" que quebrasse o fetch de produção seria pior
que a limitação declarada.

**Nota de bastidor.** Os primeiros testes usaram `203.0.113.0/24` como "IP público de exemplo" —
faixa que o Python classifica como privada desde a 3.12, então a própria cerca os rejeitava. Foi
o teste falhando por motivo certo pelo caminho errado.
