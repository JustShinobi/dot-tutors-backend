# Guia de incorporação

Como colocar um tutor no seu site. Do lado do integrador, a única coisa exigida é uma tag
`<iframe>` — sem LTI, sem SSO, sem SDK.

---

## 1. Obtenha a chave e o snippet

No painel administrativo: **Tutores → escolha o tutor → Embed**.

Crie uma chave informando as **origens permitidas** — o endereço do seu site, no formato
`https://seusite.com` (sem caminho, sem barra final). O painel devolve o snippet pronto.

```html
<iframe
  src="https://app.exemplo.com/embed/pk_live_abc123"
  title="Tutor: Onboarding"
  width="400"
  height="620"
  style="border:0;border-radius:12px"
  loading="lazy"
  referrerpolicy="strict-origin-when-cross-origin"
></iframe>
```

Cole no HTML da sua página. É isso.

---

## 2. A chave é pública — e por que isso não é um problema

A chave `pk_live_...` aparece no código-fonte da sua página e qualquer visitante consegue lê-la.
Isso é **por design**, não um descuido: tratá-la como segredo seria autoengano, porque não há
como esconder um atributo `src`.

O que realmente protege o tutor:

| Camada | O que faz |
|---|---|
| **Allowlist de origens** | O backend confere o header `Origin` do navegador contra a lista da chave. Origem não listada recebe `403` e nenhuma sessão é aberta. |
| **`frame-ancestors`** | A página do widget declara quem pode enquadrá-la. Um site não autorizado não consegue nem renderizar o iframe. |
| **Token de sessão curto** | Após a validação, o backend emite um token de ~30 minutos escopado a uma única sessão. Ele viaja no header `Authorization`, nunca em cookie nem na URL. |
| **Rate limit** | Limita mensagens por sessão e aberturas de sessão por IP. |
| **Segredo real fica no servidor** | A credencial do modelo de linguagem nunca sai do backend. |

As duas primeiras camadas são complementares, não redundantes: `frame-ancestors` impede a página
de **renderizar** em site hostil (garantia do navegador); a checagem de `Origin` impede a sessão
de **abrir** (garantia do servidor). Nenhuma sozinha basta.

---

## 3. Erros comuns

**O widget diz "Este site não está autorizado a carregar o tutor".**
A origem da sua página não está na allowlist da chave. Confira o formato: `https://seusite.com`
e `https://www.seusite.com` são origens **diferentes**, assim como `http://` e `https://`, e
portas distintas.

**O iframe fica em branco.**
O navegador bloqueou o enquadramento via `frame-ancestors`. Mesma causa: origem fora da lista,
ou chave revogada. O console do navegador mostra a violação de CSP.

**O widget diz "Este tutor está indisponível no momento".**
O tutor foi desativado no painel. Sessões existentes continuam válidas até expirarem; novas são
recusadas. Isso é intencional — desativar tira do ar sem quebrar quem está no meio de uma
conversa.

**Funciona em uma aba e não em outra.**
A sessão vive em `sessionStorage`, isolada por aba e por site hospedeiro. Cada aba abre a sua.

---

## 4. Limitações que valem conhecer

- **A conversa é anônima.** Não há login do usuário final; o histórico dura enquanto a aba
  estiver aberta.
- **Sem cookies.** O widget não usa cookie algum, justamente porque navegadores bloqueiam cookie
  de terceiro dentro de iframe. Nada a declarar em banner de cookies por causa dele.
- **Altura fixa.** O iframe não se redimensiona sozinho. Ajuste `height` conforme o seu layout.

---

## 5. Fluxo, para quem quiser entender por baixo

```
Seu site            Widget (iframe)          Backend
   │                     │                      │
   │──renderiza iframe──►│                      │
   │                     │  GET /embed/<pk>     │
   │                     │◄─ CSP frame-ancestors│  (quem pode enquadrar)
   │                     │                      │
   │                     │─ POST /embed/session ►  valida pk + Origin + tutor ativo
   │                     │◄─ token de sessao ───│
   │                     │                      │
   │                     │─ POST /embed/chat ──►│  agente consulta as fontes
   │                     │◄══ stream SSE ═══════│  token a token + citacoes
```
