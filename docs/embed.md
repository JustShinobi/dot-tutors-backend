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
O tutor foi desativado no painel. O efeito é imediato: além de recusar novas sessões, quem já
estava conversando recebe essa mensagem na próxima pergunta. Desativar tira do ar de verdade —
sem quebrar a sua página, que continua carregando o widget normalmente.

**O widget diz "A chave de incorporação é inválida ou foi revogada".**
A chave foi revogada no painel, ou o seu domínio saiu da lista de origens permitidas. Também é
imediato, pelo mesmo motivo: cada mensagem reconfere a chave e a origem contra a configuração
atual. Um token de sessão vale por até 30 minutos, e se não fosse reconferido "revogar"
significaria, na prática, "revogar daqui a meia hora".

**Funciona em uma aba e não em outra.**
A sessão vive em `sessionStorage`, isolada por aba e por site hospedeiro. Cada aba abre a sua.

---

## 4. Ajuste automático de altura (opcional)

Por padrão o iframe usa a altura fixa do snippet e o widget rola internamente. Se preferir que
ele acompanhe o tamanho da conversa, o widget publica a altura do conteúdo por `postMessage`.

```html
<iframe id="tutor" src="https://app.exemplo.com/embed/pk_live_abc123" width="400" height="620"
        style="border:0;border-radius:12px"></iframe>

<script>
  const WIDGET_ORIGIN = "https://app.exemplo.com";
  const iframe = document.getElementById("tutor");

  window.addEventListener("message", (event) => {
    // Obrigatório: qualquer frame pode postar nesta janela, então o tipo da mensagem
    // sozinho não prova nada. Sem esta linha, qualquer site consegue redimensionar o seu.
    if (event.origin !== WIDGET_ORIGIN) return;
    if (event.data?.type !== "dot-tutor:resize") return;

    // Limite o crescimento: assim o widget nunca domina a sua página.
    iframe.height = Math.min(Math.max(event.data.height, 240), 900);
  });
</script>
```

A mensagem tem o formato `{ type: "dot-tutor:resize", height: number, embedKey: string }`. O
`embedKey` vem junto para uma página que incorpore mais de um tutor conseguir distinguir os
frames. Ignorar a mensagem é uma opção legítima — nada quebra.

Implementação de referência do lado do host: `components/embed/ResizingEmbed.tsx` no repositório
do frontend, usada pela própria página `/demo`.

---

## 5. Limitações que valem conhecer

- **A conversa é anônima.** Não há login do usuário final; o histórico dura enquanto a aba
  estiver aberta.
- **Sem cookies.** O widget não usa cookie algum, justamente porque navegadores bloqueiam cookie
  de terceiro dentro de iframe. Nada a declarar em banner de cookies por causa dele.

---

## 6. Fluxo, para quem quiser entender por baixo

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
