---
name: doc-feature
description: Gera documentação pública (.mdoc) de uma feature/módulo do LeanLLM SDK na pasta de docs do SaaS (leanllm_saas/apps/web/content/documentation/features/leanllm/). Lê o módulo no TODO.md, cruza com o código real, e produz uma página pronta para o site público com frontmatter, exemplos copy-paste e referências cruzadas. Use quando precisar publicar um módulo já implementado.
---

# doc-feature — Gerador de página de documentação pública

Use esta skill para escrever a página `.mdoc` de UMA feature/módulo do LeanLLM SDK, a ser publicada no site público do SaaS. Cada invocação produz UM arquivo (mais, opcionalmente, atualização do índice).

## Argumentos

O usuário pode informar:
- Um número/nome de módulo (ex: `5`, `Replay Engine`, `12`) → trabalha apenas nesse módulo.
- Nenhum argumento → pergunta qual módulo, **não tenta documentar tudo de uma vez**.

## Pré-requisitos (sempre leia antes de começar)

1. `leanllm_lib/TODO.md` — em especial:
   - O bloco do módulo alvo (sub-itens `[x]`).
   - O bloco `### Implementation Notes` correspondente (escrito por `todo-progress`). É o ponto de partida.
2. Um arquivo `.mdoc` existente como referência de tom e formato. Sugestão: `leanllm_saas/apps/web/content/documentation/features/email.mdoc` ou `leanllm_saas/apps/web/content/documentation/getting-started/quick-start.mdoc`.
3. Os arquivos fonte mencionados em "Implementation Notes" do módulo. **As notas podem estar defasadas — o código é a fonte de verdade**. Se houver divergência, confie no código e registre a divergência ao usuário.
4. `leanllm_lib/leanllm/__init__.py` — para confirmar quais símbolos são públicos (re-exportados) vs internos.

## Mapeamento canônico módulo → caminho

Os módulos publicáveis do LeanLLM são os user-facing. Não documente módulos puramente internos (0 / 3 / 8 / 10 / 11 / 15 / 17). Use estes slugs:

| Módulo (TODO) | Slug do arquivo | Path |
|---|---|---|
| 1. Request Interception | `interception` | `features/leanllm/interception.mdoc` |
| 2. Context Propagation | `context` | `features/leanllm/context.mdoc` |
| 4. Semantic Normalization | `normalization` | `features/leanllm/normalization.mdoc` |
| 5. Deterministic Replay | `replay` | `features/leanllm/replay.mdoc` |
| 6. Lineage & Execution Graph | `lineage` | `features/leanllm/lineage.mdoc` |
| 7. Cost & Token Estimation | `cost` | `features/leanllm/cost.mdoc` |
| 9. Privacy & Redaction | `redaction` | `features/leanllm/redaction.mdoc` |
| 12. Storage Query API | `storage-query` | `features/leanllm/storage-query.mdoc` |
| 13. CLI (Logs / Replay) | `cli` | `features/leanllm/cli.mdoc` |
| 14. Runtime Toggles & Sampling | `runtime-toggles` | `features/leanllm/runtime-toggles.mdoc` |
| 16. DX Helpers | `dx-helpers` | `features/leanllm/dx-helpers.mdoc` |

Caminho absoluto raiz: `/home/homehelp-01/projects/oleve/LeanLLM/leanllm_saas/apps/web/content/documentation/`.

Se o usuário pedir um módulo fora dessa lista (ex: 0/3/8/10/11/15), **pare e explique** que aquele módulo é interno e não vai para a doc pública (a menos que o usuário insista; nesse caso você decide um slug e segue).

## Procedimento

### 1. Mapear módulo → fontes

- Achar no `TODO.md` o cabeçalho `# N. NOME`, ler até o próximo `# `.
- Confirmar checkboxes `[x]` (não documente algo que ainda está `[ ]`).
- Ler o bloco `### Implementation Notes` desse módulo.
- Para cada arquivo citado (`leanllm/<arquivo>.py`), use Read para confirmar a API real:
  - Quais funções/classes são exportadas em `__init__.py`?
  - Assinaturas atuais (kwargs keyword-only, defaults).
  - Comportamentos não óbvios (e.g., "errors bypass sampling", "auto-chain reseta em `trace()`").
- Se algo nas notes não bate com o código, **prefira o código** e mencione a divergência ao usuário ao final.

### 2. Construir o frontmatter

Use exatamente este shape (campos obrigatórios pelo renderer):

```yaml
---
title: "<Nome humano da feature>"
description: "<Uma frase explicando o valor; max ~120 chars>"
publishedAt: <YYYY-MM-DD da hoje>
order: <número — siga a ordem do mapeamento canônico acima começando em 10>
status: "published"
---
```

- `title`: human-readable (ex: `"Deterministic Replay"`, `"Cost & Token Tracking"`).
- `description`: começa com verbo no imperativo, sem hype, foca no que o dev ganha.
- `publishedAt`: data de hoje em ISO-date (você pode rodar `date -I` via Bash).
- `order`: tabela canônica abaixo (10 → 110, em incrementos de 10):
  - 1.interception → 10
  - 2.context → 20
  - 4.normalization → 30
  - 5.replay → 40
  - 6.lineage → 50
  - 7.cost → 60
  - 9.redaction → 70
  - 12.storage-query → 80
  - 13.cli → 90
  - 14.runtime-toggles → 100
  - 16.dx-helpers → 110
- `status`: sempre `"published"`.

### 3. Construir o corpo

Use **markdown puro** (sem componentes MDX customizados — o renderer não suporta). Estrutura padrão (copie e adapte):

```
## What it does

<2-4 frases. Comece com o problema que a feature resolve. Termine com a invariante mais importante (e.g. "request path nunca bloqueia"; "errors bypass sampling").>

## When to use

- <bullet 1>
- <bullet 2>
- <bullet 3>

## API

`leanllm.<symbol>` — uma frase por símbolo público. Liste só o que é re-exportado em `leanllm/__init__.py` (público) ou diretamente útil. Não exponha helpers internos como se fossem API.

### Signatures

```python
client.<method>(
    arg1: Type,
    *,
    kwarg1: Type = default,
    ...
) -> ReturnType
```

(Use os defaults reais do código. Não invente.)

## Examples

### <título do exemplo, e.g. "Replay a stored event">

```python
from leanllm import LeanLLM, LeanLLMConfig
import asyncio

# narrative... 2-3 lines max
client = LeanLLM(api_key="...", config=LeanLLMConfig(database_url="sqlite:///events.db"))

async def main():
    event = await client.get_event(event_id="...")
    ...

asyncio.run(main())
```

(Cada example precisa: import completo, ser copiar-rodar, ter um comentário curto explicando o porquê. Não esconda imports.)

### <segundo exemplo, normalmente um "edge / advanced" case>

...

## Configuration

Liste os campos do `LeanLLMConfig` relevantes a ESTA feature, mais env vars correspondentes, em formato tabela:

| Field | Env var | Default | What it does |
|---|---|---|---|
| `sampling_rate` | `LEANLLM_SAMPLING_RATE` | `1.0` | ... |

Só liste o que é desta feature. Não duplique a tabela de config inteira.

## Edge cases & gotchas

- **<heading curto>** — descrição do edge case. Cite o comportamento exato observado no código.
- ...

## See also

- [<link interno se aplicável>](/docs/features/leanllm/<outra-feature>)
- ...
```

### 4. Regras de qualidade

- **Code snippets copy-paste-able**: imports completos, sem `...` no meio que esconda algo essencial. Se um exemplo precisa de async, mostre o `asyncio.run(...)` no final.
- **Não invente API**: cada símbolo, kwarg, default ou comportamento citado tem que ter contrapartida no código. Se não tem, é tarefa para `todo-progress`, não para `doc-feature`.
- **Não cite módulo interno como público**: e.g. `_classify_error`, `_to_row`, `EventQueue`, `EventWorker`. Se precisar mencioná-los, deixe explícito que são internos.
- **Sem hype**: nada de "blazing fast", "production-ready", "powerful". Tom = FastAPI/Pydantic docs.
- **Inglês**: o site é público; toda página em inglês. Frontmatter `description` também.
- **Não duplique a tabela de pricing/config inteira** — linke para uma página de configuration central se precisar.

### 5. Atualizar o índice (se aplicável)

Se for a primeira página em `features/leanllm/`, crie também `features/leanllm/leanllm.mdoc` (índice da subseção) com:
- `order: 5` (para aparecer antes das demais features genéricas do template).
- Lista de bullets com cada página da subseção.

Se já existe, **atualize a lista** — adicione um bullet para a página recém-criada, mantendo a ordem.

### 6. Verificação

Antes de terminar:
- Releia o `.mdoc` gerado e confira que:
  - O frontmatter tem todos os 5 campos.
  - Cada code snippet roda standalone (mentalmente: imports + asyncio se precisa).
  - Não há claim sem contrapartida no código.
- Rode `ls` na pasta destino para confirmar que o arquivo foi criado.
- Se o usuário tem o dev server do Next.js rodando (`pnpm dev` em `leanllm_saas/apps/web`), abra mentalmente a URL `/docs/features/leanllm/<slug>` e confira se faz sentido.

## Saída ao usuário

Reporte em até 8 linhas:
- Caminho do arquivo criado.
- Slug e order.
- Quantos exemplos foram incluídos.
- Quaisquer divergências código ↔ TODO que você detectou (e o que decidiu).
- Próxima ação sugerida (a próxima feature do mapeamento canônico, ou rodar `pnpm dev` para ver).

## O que NÃO fazer

- Não documente vários módulos numa só invocação. Um módulo, um arquivo, uma decisão de tom por vez.
- Não use componentes MDX (`<Callout>`, `<Tabs>`, etc.) — o renderer atual não suporta.
- Não copie a "mock content" warning do template (`> **Note:** This is mock/placeholder content...`) — esse aviso só vale para os docs de exemplo do MakerKit; o nosso é real.
- Não invente env vars, kwargs ou defaults. Cada `LEANLLM_*` mencionado tem que existir no `config.py` (`from_env`).
- Não escreva em pt-br no `.mdoc`. Site público = inglês.
- Não toque em código `leanllm/` ou `tests/`. Esta skill só edita docs em `leanllm_saas/apps/web/content/documentation/features/leanllm/`.
- Não rode `git commit`. O usuário decide quando.
