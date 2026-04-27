---
name: todo-progress
description: Sincroniza o TODO.md do LeanLLM com o estado real do código. Marca itens concluídos como [x], adiciona um bloco "Implementation Notes" por módulo com detalhes (arquivos, classes, comportamentos, edge cases) que o todo-tests consome para gerar testes. Use após implementar features ou quando o TODO estiver defasado em relação ao código.
---

# todo-progress — Sincronizador do TODO do LeanLLM

Use esta skill para atualizar `TODO.md` após implementar uma ou mais features, ou quando o TODO estiver desatualizado em relação ao código em `leanllm/`.

## Argumentos

O usuário pode opcionalmente informar:
- Um número/nome de módulo (ex: `1`, `Request Interception Layer`) → trabalha apenas nesse módulo.
- Nenhum argumento → varre o TODO inteiro, módulo a módulo.

## Pré-requisitos (sempre leia antes de começar)

1. `TODO.md` — o plano completo. Use como fonte única de verdade para o que precisa existir.
2. `docs/CODEBASE_OVERVIEW.md` — mapa do código atual.
3. `docs/BACKEND_STANDARDS.md` — convenções que constrangem o shape da implementação.
4. `leanllm/__init__.py` — API pública exposta (o que realmente conta como "pronto").

## Procedimento

Para cada módulo numerado do TODO (ou apenas o especificado):

### 1. Mapeie o módulo ao código

Use Grep/Glob/Read para achar onde cada item do módulo foi (ou não) implementado. Não adivinhe — verifique o código.

Exemplos de mapeamento:
- "Capture response payload" → procure em `leanllm/client.py` e `leanllm/events/models.py`.
- "Retry mechanism" → `leanllm/events/worker.py`.
- "Token extraction / Cost calculation" → `leanllm/events/cost.py`.
- "Privacy & Redaction" → procure módulo `redaction*` ou equivalente; se não existir, marque como pendente.

### 2. Atualize os checkboxes

- `[ ]` → `[x]` apenas quando houver código que implementa o item, não só um stub ou TODO inline.
- Se um item está parcialmente feito, mantenha `[ ]` e registre isso nas notas (seção 3).
- Nunca marque `[x]` baseado em intenção ou em outro `[x]` — a evidência é o código.

### 3. Acrescente/atualize "Implementation Notes"

Logo abaixo do último item do módulo (antes do separador `---`), garanta um bloco exatamente neste formato:

```
---
### Implementation Notes

- **Files:** `leanllm/<arquivo>.py:<linha-chave>`, ...
- **Public entry points:** `ClassName.method(...)`, `function_name(...)`
- **Key behaviors / invariants:**
  - <comportamento 1 que um teste precisa conhecer>
  - <comportamento 2>
- **Edge cases observed in code:**
  - <ex: se `usage` não vem do provider, cai em `estimate_tokens`>
  - <ex: fila cheia → `put_nowait` drop silencioso + contador>
- **Thread/async boundaries:**
  - <ex: `_emit` roda na thread do chamador, worker roda em daemon com asyncio loop próprio>
- **Not yet implemented (referenced by TODO):**
  - <itens do módulo que continuam `[ ]`, com o porquê se for interessante>
- **Test hooks / seams:**
  - <pontos úteis para injetar mocks/fakes: ex. `create_store` factory, `CostCalculator._PRICING`, `EventQueue` init>
```

Regras para as notas:
- Escreva em inglês (consistência com o resto dos docs).
- Seja específico: cite arquivos, métodos e números de linha quando agregar. Nada genérico ("handles requests").
- Prefira descrever **o que o código faz**, não o que o TODO disse que deveria fazer — se divergirem, registre a divergência.
- Se o módulo é 100% "not implemented", ainda assim crie o bloco com apenas `Not yet implemented:` preenchido. Isso sinaliza para a skill `todo-tests` que não há nada a testar ainda.
- Se já existe um bloco "Implementation Notes" no módulo, **atualize** em vez de duplicar.
- Não mexa no bloco `### Tests` (criado pelo `todo-tests`). São responsabilidades separadas.

### 4. Verificação

Antes de terminar:
- Releia o diff do `TODO.md`.
- Confirme que todo `[x]` tem contrapartida no código (`grep` / `Read` rápido).
- Confirme que todo item mencionado em "Edge cases" / "Key behaviors" é verificável lendo o código (não inventado).

## Saída ao usuário

Reporte em até 6 linhas:
- Módulos processados.
- Itens marcados como concluídos (contagem ou lista curta).
- Módulos sem implementação (ainda).
- Qualquer divergência código ↔ TODO que exigiu decisão (com o que você decidiu).

## O que NÃO fazer

- Não escreva testes aqui — é trabalho da skill `todo-tests`.
- Não edite código da biblioteca. Esta skill só edita `TODO.md`.
- Não adicione itens novos ao TODO que não estavam no plano original, a menos que o usuário peça explicitamente. Você pode registrar observações em "Implementation Notes", mas não inflar o checklist.
- Não marque `[x]` em itens do topo hierárquico (ex: "Wrap LiteLLM execution entrypoint") sem que todos os sub-itens estejam marcados.
