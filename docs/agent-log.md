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
