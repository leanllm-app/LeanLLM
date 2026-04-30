# Próxima sessão — v1.0 release: README + CI/CD + docs públicas

Este é o prompt que orquestra a próxima sessão de trabalho no LeanLLM. Cole o bloco abaixo (entre as marcas `--- PROMPT START ---` / `--- PROMPT END ---`) na nova sessão. Auto mode pode ficar ativo — as tarefas são bem definidas e a maior parte é writeback determinístico.

---

## Contexto desta sessão

- Lib OSS: `/home/homehelp-01/projects/oleve/LeanLLM/leanllm_lib/`
- SaaS (docs públicas): `/home/homehelp-01/projects/oleve/LeanLLM/leanllm_saas/`
- Estado atual: **v0.8.0**, 310 testes passando, todos os 18 módulos de roadmap completos (ver `leanllm_lib/TODO.md` § 17).
- Memórias relevantes em `/home/homehelp-01/.claude/projects/-home-homehelp-01-projects-oleve-LeanLLM/memory/MEMORY.md` — em especial:
  - request path nunca bloqueia
  - sem disco em containers efêmeros
  - SDK roda standalone, SaaS deferred
- Skills disponíveis em `leanllm_lib/.claude/skills/`:
  - `doc-feature` — gera 1 página `.mdoc` por módulo no SaaS docs.
  - `todo-progress` / `todo-tests` (existentes; provavelmente não vai precisar nesta sessão).

## Objetivos (3 fases)

### Fase 1 — README robusto da lib

Local: `/home/homehelp-01/projects/oleve/LeanLLM/leanllm_lib/README.md`

Substituir o README atual por uma versão production-grade. Estrutura:

1. **Hero** — uma frase. Ex: "LeanLLM — observability layer for LLM calls (LiteLLM-based). Capture every request, replay any of them, never block the request path."
2. **Badges** — CI, PyPI version, Python versions, License (MIT). Use os shields.io padrões.
3. **Quick install** — `pip install leanllm-ai` + extras opcionais (`[postgres]`, `[sqlite]`, `[remote]`, `[dev]`).
4. **60-second example** — bloco de código copy-paste rodável (use SQLite `:memory:` para não exigir Postgres). Mostre o caminho `chat → event capturado → query → replay`.
5. **Why LeanLLM** — 3-4 bullets curtos: transparente, sem lock-in (LiteLLM bridge), backends próprios (Postgres/SQLite), zero blocking I/O na request path.
6. **Features** — tabela linkando para cada página do site público (`https://...your-saas-domain.../docs/features/leanllm/<slug>`). Use os mesmos slugs do mapeamento canônico da skill `doc-feature`.
7. **Configuration** — tabela compacta com env vars principais (`LEANLLM_API_KEY`, `LEANLLM_DATABASE_URL`, `LEANLLM_REDACTION_MODE`, `LEANLLM_SAMPLING_RATE`, `LEANLLM_AUTO_NORMALIZE`, `LEANLLM_DEBUG`). Linke para a página completa no SaaS.
8. **Backends** — uma seção curta cobrindo os 3 stores (Postgres, SQLite, Remote stub) com 1 frase cada.
9. **Development** — clone → venv → `pip install -e ".[dev,sqlite,postgres,remote]"` → `pytest tests/ -v` → `ruff check . && ruff format --check .`. Aponte para `CLAUDE.md` para regras detalhadas.
10. **Contributing** — link para CONTRIBUTING.md (criar se não existir; pode ser stub apontando para issues + PR template).
11. **License** — MIT (já existe `LICENSE`).

**Regras:**
- Tom: FastAPI / Pydantic / SQLModel. Sem hype, sem "blazing fast", sem emojis (a menos que o usuário peça explicitamente).
- Toda code snippet copia e roda. Imports completos. Sem `...` que esconda algo necessário.
- Não invente features. Se não tem no `TODO.md` ou no código, não vai pro README.
- Mantenha versão alinhada com `pyproject.toml` e `leanllm/__init__.py:__version__`.

**Validação:** ao final, rode `pytest tests/ -v` para garantir que nada quebrou (pode ter mudado um docstring por engano). E pinte mentalmente: o snippet do "60-second example" roda?

### Fase 2 — CI/CD

Local: `/home/homehelp-01/projects/oleve/LeanLLM/leanllm_lib/.github/workflows/`

Criar dois workflows:

**`ci.yml`** — disparado em `push` para qualquer branch e `pull_request` para `main`:
- `runs-on: ubuntu-latest`
- Matrix Python: `["3.10", "3.11", "3.12", "3.13"]`
- Steps:
  1. `actions/checkout@v4`
  2. `actions/setup-python@v5` com a matrix version + cache `pip`
  3. `pip install -e ".[dev,sqlite,postgres,remote]"`
  4. `pytest tests/ -v`
  5. `ruff check leanllm/ tests/`
  6. `ruff format --check leanllm/ tests/`
- Jobs falham se qualquer step falhar. Não use `continue-on-error`.

**`publish.yml`** — disparado em `push` de tags `v*.*.*`:
- `runs-on: ubuntu-latest`
- Steps:
  1. `actions/checkout@v4`
  2. `actions/setup-python@v5` (use Python 3.12 como referência de build)
  3. Asserir que a tag bate com a versão do `pyproject.toml`. Algo como:
     ```bash
     TAG_VERSION="${GITHUB_REF#refs/tags/v}"
     PKG_VERSION=$(python -c "import tomllib,pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")
     test "$TAG_VERSION" = "$PKG_VERSION" || (echo "tag $TAG_VERSION != pyproject $PKG_VERSION" && exit 1)
     ```
  4. `pip install build twine`
  5. `python -m build`
  6. `twine check dist/*`
  7. `twine upload dist/*` com `TWINE_USERNAME=__token__` e `TWINE_PASSWORD=${{ secrets.PYPI_API_TOKEN }}`.
  8. Criar GitHub Release com `gh release create "v$TAG_VERSION" --generate-notes dist/*`. Use `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`.

**Regras:**
- Não use `--no-verify`, `continue-on-error`, ou skips silenciosos.
- Se ruff/format reclamar, conserte o código. Não silencie regras.
- Não pushe direto pra `main` — sempre PR.
- Se a versão do `pyproject.toml` ainda for `0.8.0` mas você está documentando como release `1.0.0`, **bump explícito** em `pyproject.toml` E `leanllm/__init__.py:__version__` antes de tagear.

**Validação:** rode `act` localmente se disponível (opcional); senão, pelo menos lint o YAML com `yamllint .github/workflows/ci.yml` ou similar. Para o `publish.yml`, dry-run mental: tag → assert versão → build → twine check → upload → release.

### Fase 3 — Documentação pública

Local: `/home/homehelp-01/projects/oleve/LeanLLM/leanllm_saas/apps/web/content/documentation/`

Criar a subseção `features/leanllm/` com:

**3a) Índice da subseção** — `features/leanllm/leanllm.mdoc`:
- title: "LeanLLM SDK"
- description: "Observability layer for LLM calls — interception, replay, lineage, cost tracking."
- order: 5
- Body: 1 parágrafo de overview + lista bulletada com bullet por página criada (cada bullet linka para `/docs/features/leanllm/<slug>`).

**3b) Getting-started específicas do LeanLLM** (3 páginas, escritas inline nesta sessão; **não** use a skill `doc-feature` para essas — a skill é para módulos de feature):

- `features/leanllm/installation.mdoc` (order 6) — pip install + extras + descrição rápida do que cada extra liga.
- `features/leanllm/quick-start.mdoc` (order 7) — exemplo end-to-end: instalar → configurar SQLite → primeira chamada → ver evento via `client.last_event` → query via `client.list_events()` → replay via `client.get_event()` + `ReplayEngine`.
- `features/leanllm/configuration.mdoc` (order 8) — tabela completa de `LeanLLMConfig` + env vars. **Esta página é a referência canônica** para a tabela de config; o README e as features individuais devem linkar para cá em vez de duplicar.

Tom: igual ao da skill (FastAPI-style, exemplos copy-paste, sem hype).

**3c) Páginas por feature** — usar a skill `/doc-feature <module>` uma vez por módulo. Mapeamento canônico (já está na skill):

| Módulo | Slug |
|---|---|
| 1. Request Interception | interception |
| 2. Context Propagation | context |
| 4. Semantic Normalization | normalization |
| 5. Deterministic Replay | replay |
| 6. Lineage & Execution Graph | lineage |
| 7. Cost & Token Estimation | cost |
| 9. Privacy & Redaction | redaction |
| 12. Storage Query API | storage-query |
| 13. CLI (Logs / Replay) | cli |
| 14. Runtime Toggles & Sampling | runtime-toggles |
| 16. DX Helpers | dx-helpers |

Sequência sugerida: 1 → 2 → 4 → 5 → 6 → 7 → 9 → 12 → 13 → 14 → 16. Após CADA invocação da skill, atualize o `features/leanllm/leanllm.mdoc` adicionando o bullet correspondente (a skill instrui isso).

**3d) Validação visual:**
- `cd /home/homehelp-01/projects/oleve/LeanLLM/leanllm_saas && pnpm dev` (ou o comando equivalente do MakerKit)
- Abrir `http://localhost:3000/docs/features/leanllm/leanllm` e navegar entre as páginas
- Confirmar que cada página renderiza sem erro de markdown e que os links internos funcionam

## Ordem de execução (estritamente)

1. **Fase 1** primeiro (README) — é a porta de entrada do projeto.
2. **Fase 2** segundo (CI/CD) — antes de gerar mais conteúdo, garante que qualquer commit já roda na CI.
3. **Fase 3** por último (docs) — itere por feature, commit por feature. Use a skill.

## Critérios de pronto da sessão

- [ ] `README.md` substituído, snippet do "60-second example" roda quando colado num shell limpo (após `pip install -e ".[sqlite]"`).
- [ ] `pytest tests/ -v` ainda passa (310+).
- [ ] `.github/workflows/ci.yml` e `publish.yml` criados; YAML válido.
- [ ] Subseção `features/leanllm/` criada com índice + 3 getting-started + 11 páginas de feature = **15 arquivos `.mdoc`** novos.
- [ ] Cada página tem frontmatter completo (`title`, `description`, `publishedAt`, `order`, `status`).
- [ ] `pnpm dev` no SaaS renderiza todas as páginas sem erro.
- [ ] Versão bumpada para `1.0.0` em `pyproject.toml` + `leanllm/__init__.py` (a sessão termina com a v1.0 pronta para tag, ainda **sem** dar push da tag — o usuário decide quando).

## O que NÃO fazer nesta sessão

- Não dê push em tag — apenas prepare o release.
- Não publique no PyPI — o `publish.yml` faz isso quando o usuário tagar.
- Não toque em código `leanllm/` ou `tests/`. Se descobrir um bug enquanto escreve docs, **pare e relate ao usuário** — fixing fica para outra sessão.
- Não invente features que não estão no código (cross-check obrigatório com `__init__.py` exports + arquivos fonte).
- Não crie skills novas. Se faltar uma, relate.

---

## --- PROMPT START ---

Cole o bloco abaixo na próxima sessão.

```
Estou iniciando a sessão de release v1.0 do LeanLLM SDK. O plano completo está em
/home/homehelp-01/projects/oleve/LeanLLM/leanllm_lib/.claude/NEXT_SESSION_PROMPT.md
— leia esse arquivo primeiro e siga as 3 fases na ordem (README → CI/CD → docs).

Skills relevantes (todas em /home/homehelp-01/projects/oleve/LeanLLM/leanllm_lib/.claude/skills/):
- /doc-feature  — para cada uma das 11 páginas de feature; uma invocação por módulo
- /todo-progress, /todo-tests — não devem ser necessárias nesta sessão

Critérios de pronto: README substituído, ci.yml + publish.yml criados, 15 arquivos
.mdoc novos em leanllm_saas/apps/web/content/documentation/features/leanllm/, versão
bumpada para 1.0.0 (sem push de tag — eu faço isso depois). Suite de testes (310+)
deve continuar passando.

Comece pela Fase 1.
```

## --- PROMPT END ---
