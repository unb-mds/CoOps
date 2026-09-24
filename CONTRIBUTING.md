# Contribuindo para CoOps – Collaboration & Ops Metrics

Obrigado por dedicar seu tempo para contribuir! Este projeto é licenciado sob **GPL-3.0-only (ou posterior)** e buscamos construir uma comunidade acolhedora, transparente e colaborativa.

> TL;DR
> 1. Faça um fork e crie uma branch a partir de `main` usando Conventional Commits no nome.
> 2. `uv sync` e garanta que os comandos rodam (`uv run coops-bronze`, `uv run pytest`).
> 3. Adicione/ajuste testes (quando aplicável) e execute validações.
> 4. Atualize documentação se o comportamento público mudar.
> 5. Abra o PR seguindo o checklist.
>
> Sempre respeite nosso [Código de Conduta](CODE_OF_CONDUCT.md).

---
## Licenciamento das Contribuições
Ao contribuir, você concorda que sua contribuição será licenciada sob a **GNU General Public License v3.0 ou posterior**. Se incluir arquivos novos de código, adicione no topo (quando aplicável):
```
# CoOps – Collaboration & Ops Metrics Dashboard
# Copyright (C) 2025 CoOps Contributors
# Licensed under the GNU General Public License v3.0 (or later). See LICENSE.
```
Para JSON/MD utilize comentários no PR descrevendo a autoria.

---
## Arquitetura Rápida
- Bronze: extração da API do GitHub (dados crus)
- Silver: processamento e enriquecimento analítico
- Gold: KPIs executivos

Detalhes: veja `ARCHITECTURE.md`.

---
## Pré-requisitos

- **Python 3.10+**
- **Node.js 18+** e **npm/yarn**
- **Git** configurado
- **GitHub Account** com acesso ao repositório
- **GitHub Personal Access Token** (com permissões `repo` e `read:org`)

---
## Ambiente de Desenvolvimento

### Backend (Python)

1. **Instalar dependências e o pacote** (cria o virtualenv automaticamente):
   ```bash
   uv sync
   ```
   Isso instala o pacote `coops` e os comandos `coops-bronze`, `coops-silver`,
   `coops-gold`, `coops-aggregate` e `coops-registry`. É o mesmo comando que a CI roda.

   Sem uv: `python -m venv .venv && source .venv/bin/activate && pip install -e . --group dev` (pip >= 25.1).

2. **Configurar credenciais GitHub:**

   Crie um arquivo `.secrets` na raiz do projeto:
   ```
   GITHUB_TOKEN=seu_token_aqui
   GITHUB_ORG=unb-mds
   ```

3. **Executar pipeline manual** (a organização alvo vem de `GITHUB_ORG` no `.secrets` ou no ambiente):
   ```bash
   uv run coops-bronze --cache
   uv run coops-silver
   uv run coops-gold
   uv run coops-aggregate
   uv run coops-registry
   ```
   Os comandos escrevem em `./data` e `./cache`; na raiz do repositório eles sobrescrevem os arquivos rastreados `data/master_registry.json` e `data/data_catalog.json` (não commite essas mudanças). Para manter o checkout limpo, rode de outro diretório com `uv run --project <caminho-do-CoOps> ...`.

Para simular GitHub Actions localmente e conectar o frontend aos dados gerados (opcional): consulte `RUNNING_LOCALLY.md`.

### Frontend (React)

1. **Navegar para o diretório:**
   ```bash
   cd dashboard
   ```

2. **Instalar dependências:**
   ```bash
   npm install
   ```

3. **Rodar em desenvolvimento:**
   ```bash
   npm run dev
   ```

4. **Acessar:**
   ```
   http://localhost:5173
   ```

---
## Fluxo de Branches

- `main`: sempre estável, e recebe **poucos merges verificados** — não um fluxo contínuo.
- `phase/<épico>-<slug>`: branch de integração de um épico. É **daqui** que sai
  o trabalho de uma issue, e é esta branch que abre PR para `main` quando o
  épico está consolidado e testado por inteiro.
- Branches de feature seguem o padrão:

```
main (produção)
  └── phase/107-raw-capture          (branch de integração do épico)
        ├── feat/minha-feature       (novas funcionalidades)
        ├── fix/nome-do-bug          (correções)
        ├── refactor/nome-refactor   (melhoria interna sem mudar comportamento externo)
        └── test/nome-do-teste       (testes)
```

**Exceção:** um PR de ferramental ou documentação pode ir direto para `main`
quando o diff **não toca nada em `src/`, `data/` ou `dashboard/`** — definições
de agente, CI, docs, scripts. Qualquer coisa que toque esses diretórios disputa
com a branch de fase e precisa passar por ela.

**Antes de começar, sincronize a branch de fase com `main`.** Uma branch de fase
de vida longa acumula divergência silenciosamente: as regras de teste, o
`definition-of-done.md` e as instruções dos agentes vivem em `main`, e quem sai
de uma fase desatualizada trabalha com a versão antiga delas sem nenhum aviso.

```bash
git checkout phase/<épico>-<slug>
git merge origin/main          # normalmente só docs; resolva antes de ramificar
```

### Workflow Padrão

1. **Atualizar a branch de fase:**
   ```bash
   git fetch origin
   git checkout phase/107-raw-capture
   git merge origin/main
   ```

2. **Criar branch da feature a partir da fase:**
   ```bash
   git checkout -b feat/issue-42-dashboard-metricas
   ```

3. **Fazer alterações e commits:**
   ```bash
   git add .
   git commit -m "feat(dashboard): add metrics visualization"
   ```

4. **Push da branch:**
   ```bash
   git push origin feat/issue-42-dashboard-metricas
   ```

5. **Abrir Pull Request como draft** no GitHub

6. **Validar localmente com `gh act`** e marcar o PR como *Ready for review*.
   A validação por *dispatch* na organização foi aposentada: os workflows rodam
   na máquina de quem desenvolve, sem precisar de acesso de escrita a nenhum
   fork. Passo a passo em [docs/local-actions.md](docs/local-actions.md) e
   [docs/TESTING_PULL_REQUESTS.md](docs/TESTING_PULL_REQUESTS.md).

   > Use a extensão `gh act`, não o binário `/usr/local/bin/act`, que é uma
   > build antiga com CVE-2026-34041 e CVE-2026-34042.

7. **Code Review** e aprovação — por quem **não** escreveu nem despachou a
   mudança. A aprovação vale para **um commit**: se a cabeça da branch mudar
   depois dela, a aprovação caduca e o PR precisa ser revisto de novo.

8. **Merge** para a **branch de fase** (via Squash and Merge). O merge para
   `main` acontece uma vez, quando a fase inteira está pronta.

   > O corpo do PR de fase→`main` precisa **repetir a lista de `Closes #N`**.
   > Um `Closes` que dispara contra uma branch de fase é ignorado pelo GitHub e
   > a palavra-chave é gasta — as issues não fecham sozinhas no merge final.

---
## Commits (Conventional Commits)

Seguimos o padrão [Conventional Commits](https://www.conventionalcommits.org/).

**Formato:** `<type>(escopo opcional): descrição curta`

### Tipos de Commit

- `feat`: Nova funcionalidade
- `fix`: Correção de bug
- `docs`: Documentação
- `style`: Formatação (não afeta código)
- `refactor`: Refatoração
- `test`: Adicionar/modificar testes
- `chore`: Tarefas de manutenção
- `perf`: Melhoria de performance
- `ci`: Mudanças em CI/CD

### Exemplos

```bash
feat(bronze): adicionar cache para commits
fix(silver): corrigir cálculo de cycle time
docs: atualizar seção de execução local
refactor(bronze): extract API client to utils
```

> Descrição imperativa, <= 72 caracteres. Use corpo para detalhes.

### Boas Práticas

- Commits atômicos: um commit = uma mudança lógica
- Mensagens claras: descreva o "o quê" e "por quê"
- Presente do indicativo: "add feature" não "added feature"
- Limite 50 caracteres no título
- Linha em branco entre título e corpo
- Evitar commits genéricos: "fix bug", "update code"

---
## Checklist para Pull Requests

Antes de abrir o PR:
- [ ] Issue relacionada vinculada (`Fixes #123` ou `Closes #123`)
- [ ] Descrição clara do objetivo
- [ ] Scripts relevantes testados localmente
- [ ] Sem regressões aparentes em arquivos de dados críticos
- [ ] Documentação atualizada (`README`, `ARCHITECTURE`, etc.)
- [ ] Cobertura de casos edge (quando relevante)
- [ ] Commits seguem Conventional Commits
- [ ] Sem arquivos temporários (cache local, credenciais, etc.)
- [ ] Testes adicionados/atualizados
- [ ] CI/CD passando
- [ ] Validação na organização (`unb-mds/CoOps`) verde para o último commit ([docs/TESTING_PULL_REQUESTS.md](docs/TESTING_PULL_REQUESTS.md))

### Template sugerido no PR
```
### Contexto
(Explique o problema ou oportunidade)

### O que foi feito
- ...

### Como validar
Passos para reproduzir/verificar.

### Riscos / Observações
- ...

### Screenshots (se aplicável)
```

---
## Code Review

### Para Autores

**Antes de solicitar review:**
- Código compila/roda sem erros
- Testes passam
- CI/CD está verde
- Self-review feito
- Documentação atualizada

**Durante review:**
- Responder comentários rapidamente
- Fazer commits de fix separados
- Agradecer sugestões

### Para Reviewers

**Responsabilidades:**
- Revisar em até **24 horas**
- Testar código localmente (se necessário)
- Comentários construtivos
- Sugerir melhorias
- Aprovar quando satisfatório

**Checklist de Review:**
- [ ] **Funcionalidade:** Código faz o que deveria?
- [ ] **Testes:** Tem cobertura adequada?
- [ ] **Performance:** Há gargalos óbvios?
- [ ] **Segurança:** Há vulnerabilidades?
- [ ] **Legibilidade:** Código é claro?
- [ ] **Documentação:** Está atualizada?
- [ ] **Style Guide:** Segue padrões?

**Tipos de Comentários:**
- **BLOCKER:** Deve ser corrigido antes do merge.
- **SUGGESTION:** Nice to have, melhoria sugerida.
- **NIT:** Estilo/preferência, não bloqueia.
- **QUESTION:** Pedido de esclarecimento.

---
## Padrões de Código

### Python

**Style Guide:** [PEP 8](https://pep8.org/)

```python
# Bom
def extract_repository_data(repo_name: str) -> dict:
    """
    Extract repository data from GitHub API.

    Args:
        repo_name: Name of the repository

    Returns:
        Dictionary with repository data
    """
    response = github_api.get_repository(repo_name)
    return response.json()
```

**Ferramentas:**
- **Formatter:** `black`
- **Linter:** `flake8` ou `pylint`
- **Type Checker:** `mypy`

```bash
black src/
flake8 src/
mypy src/
```

### TypeScript/React

**Style Guide:** [Airbnb Style Guide](https://github.com/airbnb/javascript/tree/master/react)

```typescript
interface RepoData {
  name: string;
  stars: number;
  language: string;
}

export const RepoCard: React.FC<{ data: RepoData }> = ({ data }) => {
  return (
    <div className="repo-card">
      <h3>{data.name}</h3>
      <p>{data.stars}</p>
      <span>{data.language}</span>
    </div>
  );
};
```

**Ferramentas:**
- **Formatter:** `prettier`
- **Linter:** `eslint`

```bash
npm run format
npm run lint
npm run lint:fix
```

---
## Testes e Validação

### Estrutura de Testes

```
tests/
├── unit/           # Testes unitários
│   ├── test_api_client.py
│   └── test_data_processing.py
├── integration/    # Testes de integração
│   └── test_etl_pipeline.py
└── e2e/            # Testes end-to-end
    └── test_dashboard.spec.ts
```

### Python - pytest

Prefixe com `uv run` (ou ative o venv com `source .venv/bin/activate`):

```bash
# Todos os testes
uv run pytest

# Com cobertura
uv run pytest --cov=coops

# Teste específico
uv run pytest tests/unit/test_api_client.py::test_get_repository_success

# Verbose
uv run pytest -v
```

### TypeScript/React - Vitest

```bash
# Todos os testes
npm test

# Watch mode
npm test -- --watch

# Cobertura
npm test -- --coverage
```

### Cobertura Esperada

- **Mínimo:** 70% cobertura geral
- **Crítico:** 90% para funções de API/ETL
- **Frontend:** 60% (componentes principais)

Incentiva-se:
- Testes unitários para utilitários críticos (ex.: normalização, agregações)
- Verificação de integridade de JSON gerados (schemas simples)
- Scripts de sanity check (ex.: tamanho > 0, chaves esperadas)

---
## Estrutura de Dados Sensíveis
Não commitar:
- Tokens
- Dumps privados
- Arquivos `.secrets`

Use `.gitignore` conforme necessário.

---
## Reportando Bugs
Abrir issue com:
- Passos para reproduzir
- Comando(s) executado(s)
- Output relevante / stack trace
- Ambiente (OS, versão Python)

---
## Sugerindo Funcionalidades
Abra issue `feature request` descrevendo:
- Problema / motivação
- Exemplo de uso
- Métrica / dado necessário
- Possíveis impactos na arquitetura

---
## Segurança
Vulnerabilidades: NÃO abra issue pública. Envie email para: `security-github-metrics@proton.me` (placeholder) com:
- Descrição da vulnerabilidade
- Passos de exploração
- Impacto
- Sugestão de mitigação

Veja mais em `SECURITY.md` (quando disponível).

---
## Código de Conduta
Conforme [Código de Conduta](CODE_OF_CONDUCT.md). Ao participar você concorda em respeitá-lo.

---
## Releases
Planejado: versionamento SemVer pós-estabilização inicial.

---
## FAQ

**Q: Preciso criar issue antes de abrir PR?**
A: Sim, para features. Para fixes pequenos, pode abrir PR direto.

**Q: Quantos reviewers preciso?**
A: Mínimo 1 aprovação para merge.

**Q: Posso fazer force push?**
A: Não após code review iniciado. Antes, apenas se necessário.

**Q: Como atualizar minha branch com main?**
A: `git checkout main && git pull && git checkout sua-branch && git rebase main`

**Q: Testes são obrigatórios?**
A: Sim para funções críticas. PRs sem testes para código novo não serão aprovados.

---
## Discussões
Para dúvidas de arquitetura ou métricas abra uma issue `question`.

---
## Reconhecimento
Contribuidores serão listados no futuro em seção de agradecimentos / `AUTHORS.md`.

Obrigado por ajudar a construir um ecossistema de colaboração saudável!

---
## Maintainership

CoOps follows a benevolent-maintainer governance model. Decisions on roadmap,
breaking changes, and release tagging rest with the current maintainers:

| Role | Name | Affiliation | Contact |
|---|---|---|---|
| Lead maintainer | Danrley Willyan da Silva Pereira | UDF | <danrley.pereira@cs.udf.edu.br> |
| Co-maintainer (advisor) | Kerlla de Souza Luz | UDF | <kerlla.luz@udf.edu.br> |
| Co-development partner | Carla Silva Rocha Aguiar | UnB | — |

**Response-time expectations.** Issues and pull requests are reviewed on a
best-effort basis, typically within two weeks during academic terms. CoOps is
developed as part of the PIBIT 2025–2026 scholarship at UDF; activity may slow
during exam periods and end-of-semester deadlines, but the project remains
maintained.

**Adding maintainers.** A contributor with sustained involvement (multiple
substantive PRs and active issue triage over at least one semester) may be
invited by the existing maintainers. New maintainers are added by consensus of
the current group.

**Security disclosures.** Do not file security issues publicly. Follow
[SECURITY.md](SECURITY.md) instead.
