---
name: todo-tests
description: Para cada módulo do TODO.md do LeanLLM, lista os testes unitários que devem existir (como checklist no próprio TODO) e cria os testes em tests/. Consome o bloco "Implementation Notes" que o todo-progress escreveu. Use quando um módulo já foi implementado e precisa de cobertura de testes.
---

# todo-tests — Gerador de testes a partir do TODO

Use esta skill para, módulo por módulo do `TODO.md`, (1) planejar o checklist de testes dentro do próprio TODO e (2) escrever os testes reais em `tests/`.

## Argumentos

O usuário pode informar:
- Um número/nome de módulo (ex: `1`, `Request Interception Layer`) → trabalha só nele.
- Nenhum argumento → varre todos os módulos que têm pelo menos um item `[x]` e ainda não têm bloco `### Tests` completo.

## Pré-requisitos (sempre leia antes de começar)

1. `TODO.md` — especialmente o bloco `### Implementation Notes` de cada módulo (escrito pela skill `todo-progress`). Se um módulo não tem esse bloco, **pare e peça ao usuário para rodar `/todo-progress` primeiro** — sem as notas, você vai inventar testes que não mapeiam o código real.
2. `docs/BACKEND_STANDARDS.md` — convenções obrigatórias (keyword-only args, Pydantic em bordas, enums, unidades em nomes, etc.). Testes devem respeitá-las.
3. `CLAUDE.md` — convenções de teste: `leanllm/X.py` → `tests/test_X.py`, cobrir happy path + erro + borda.
4. `tests/` — testes existentes, para não duplicar casos nem divergir de estilo.
5. Os arquivos fonte mencionados em "Implementation Notes" do módulo — é o ground truth do que testar.

## Procedimento

Para cada módulo alvo:

### 1. Planejar os testes dentro do TODO

Logo abaixo do bloco `### Implementation Notes` do módulo, acrescente (ou atualize) um bloco exatamente neste formato:

```
### Tests

**Target file(s):** `tests/test_<modulo>.py` (map 1:1 com o arquivo fonte quando possível)

**Cases to cover:**
- [ ] <descrição curta e específica do caso de teste 1>
- [ ] <caso 2>
- [ ] <caso 3 — edge: ...>
- [ ] <caso 4 — error: ...>
```

Regras para o checklist:
- **Um caso por linha.** Nada de agrupar ("testa várias coisas").
- Cada caso deve mapear a um item `[x]` do módulo **ou** a uma entrada de "Edge cases observed in code" / "Key behaviors". Não invente testes que não se ancoram em código existente.
- Classifique implicitamente: happy path, edge, error. Use o prefixo `edge:` ou `error:` quando não for happy path, para o leitor humano distinguir.
- Se um item do módulo está `[ ]` (não implementado), **não** gere caso de teste para ele ainda — deixe registrado em uma linha `<!-- pending: <item> não implementado -->` para rastreabilidade.
- Respeite as fronteiras de thread/async apontadas nas notas: se a feature roda no worker daemon, o teste precisa lidar com isso (fake store síncrono, drenar fila manualmente, ou equivalente — não sleep cego).

### 2. Escrever os testes

Para cada caso `[ ]` do checklist:

- Arquivo alvo: `tests/test_<modulo>.py`. Se já existir, **adicione** ao existente; não reescreva.
- Use `pytest` (já é a convenção do projeto). Funções `test_*`, sem classes a menos que o arquivo existente já use classes.
- Estilo: segue as regras do `BACKEND_STANDARDS.md`. Em particular:
  - `*` keyword-only em funções de 2+ params.
  - Pydantic para contratos cruzando fronteira; primitivas dentro.
  - Enums em vez de strings mágicas.
  - `None` para ausência; nada de sentinel strings/ints.
  - Try/except **não** é controle de fluxo — no teste, prefira `pytest.raises`.
- **Sem mocks da DB** se houver alternativa viável: para storage, use `SQLiteEventStore` em `:memory:` ou um `BaseEventStore` fake. Para LiteLLM, mock apenas a fronteira do `proxy.chat_completion` — não o objeto `LeanLLM` inteiro.
- **Nunca bloqueie o request path nos testes.** Se a feature depende do worker daemon, use um fake store que registra chamadas e drene com um timeout pequeno e determinístico (ex: aguardar evento `threading.Event` setado pelo fake, max 2s).
- Não crie helpers que serão usados uma única vez. Tudo inline a menos que repita em 3+ testes.
- Imports no topo do arquivo, sempre.

Após escrever cada caso, marque `[x]` no checklist do TODO.

### 3. Rodar e verificar

```bash
source venv/bin/activate
pytest tests/test_<modulo>.py -v
```

- Todos os novos devem passar. Os existentes também.
- Se um teste falhar porque o código implementa algo diferente do que as notas dizem, **não "corrija" o teste para passar** — pare, informe ao usuário a divergência, e proponha: (a) atualizar as notas via `/todo-progress`, (b) corrigir o código, ou (c) ajustar o teste se foi você que errou o entendimento.
- Se falhar por infra (venv ausente, extras não instalados), informe o comando que o usuário precisa rodar e pare.

### 4. Self-review antes de encerrar

Releia seu diff e remova:
- Testes que só validam tipagem do Pydantic (o próprio Pydantic já faz).
- Assertions de "smoke" que não diferenciam sucesso de regressão.
- Docstrings em testes curtos — o nome da função já diz.
- `try/except` em volta do que deveria ser `pytest.raises`.
- Fixtures criadas para uso único.

## Saída ao usuário

Reporte em até 8 linhas:
- Módulo(s) trabalhados.
- Nº de casos planejados, nº de casos escritos, nº pendentes.
- Arquivo(s) de teste criados/atualizados.
- Resultado do `pytest` (PASS/FAIL + contagem).
- Qualquer divergência código ↔ notas que você detectou.

## O que NÃO fazer

- Não edite `leanllm/` — esta skill é somente `tests/` + checklist no `TODO.md`.
- Não gere testes para itens ainda `[ ]` no TODO (não implementados).
- Não duplique cobertura que já existe em `tests/`.
- Não pule testes com `@pytest.mark.skip` sem justificativa explícita no próprio marker.
- Não rode `pytest --no-verify`-equivalentes (ex: `-x` esconde falhas importantes em suíte nova). Rode a suíte completa do módulo.
- Não use `git commit` — o usuário decide quando commitar.
