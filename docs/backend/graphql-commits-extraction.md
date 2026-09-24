# Extração de Commits com GraphQL

## Visão Geral

Este módulo implementa a extração detalhada de commits usando a API GraphQL do GitHub, permitindo obter informações sobre adições e remoções de código que não estão disponíveis na REST API padrão.

## Por que GraphQL?

A REST API do GitHub tem limitações:
- **Rate Limit**: 5.000 requisições/hora (autenticado)
- **Dados limitados**: Commits básicos não incluem `additions` e `deletions`
- **Múltiplas requisições**: Necessário fazer requisições adicionais para obter detalhes de cada commit

A GraphQL API oferece:
- **Rate Limit mais generoso**: Baseado em pontos, não em número de requisições
- **Dados completos**: Inclui `additions`, `deletions`, e `changedFiles` em uma única query
- **Menos requisições**: Busca todos os dados necessários de uma vez
- **Paginação eficiente**: Cursor-based pagination

## Estrutura de Arquivos

```
src/coops/
├── utils/
│   └── github_api.py           # Cliente REST + GraphQL
├── bronze/
│   └── commits.py              # Extração de commits (REST + GraphQL)
└── etl/
    └── bronze_extract.py       # Orquestrador (comando `coops-bronze`)
```

## Como Usar

O token e a organização vêm de `GITHUB_TOKEN` / `GITHUB_ORG` (ambiente,
`.env` ou `.secrets`):

```bash
export GITHUB_TOKEN=YOUR_GITHUB_TOKEN
export GITHUB_ORG=unb-mds
```

### 1. Teste Rápido

Extração GraphQL limitada a poucos repositórios e commits:

```bash
uv run coops-bronze \
  --commits-method graphql \
  --max-repos 1 \
  --max-commits-per-repo 50 \
  --skip-structure
```

### 2. Extração Completa com GraphQL

Execute a extração bronze completa usando GraphQL para commits (padrão):

```bash
uv run coops-bronze --commits-method graphql
```

### 3. Limitar Commits por Repositório

Para organizações grandes, limite o número de commits por repo:

```bash
uv run coops-bronze \
  --commits-method graphql \
  --max-commits-per-repo 1000
```

### 4. Modo Compatível (REST API)

Continue usando a REST API se preferir (sem dados de additions/deletions):

```bash
uv run coops-bronze --commits-method rest --cache
```

## Dados Extraídos

### Estrutura do Commit (GraphQL)

```json
{
  "sha": "abc123...",
  "message": "Add feature X",
  "committed_date": "2025-11-03T10:30:00Z",
  "author": {
    "name": "João Silva",
    "email": "joao@example.com",
    "login": "joaosilva"
  },
  "committer": {
    "name": "João Silva",
    "email": "joao@example.com",
    "login": "joaosilva"
  },
  "additions": 150,
  "deletions": 23,
  "changed_files": 5,
  "total_changes": 173,
  "is_merge": false,
  "parent_count": 1,
  "tree_sha": "def456...",
  "repo_name": "my-repo",
  "full_name": "my-org/my-repo"
}
```

### Campos Importantes

| Campo | Descrição |
|-------|-----------|
| `additions` | Linhas de código adicionadas |
| `deletions` | Linhas de código removidas |
| `changed_files` | Número de arquivos modificados |
| `total_changes` | Total de linhas alteradas (additions + deletions) |
| `is_merge` | Se é um merge commit |
| `parent_count` | Número de commits pais |

> **Privacidade (emails).** Os dados de commit são commitados em um branch
> público, então o email bruto do autor **não** é persistido. Autores vinculados
> a uma conta do GitHub mantêm `login` + `id` (numérico); autores não vinculados
> recebem `author_email_hash` (SHA-256 do email com espaços removidos e em
> minúsculas) no lugar do endereço. Trata-se de pseudonimização, não de
> anonimização.

## Arquivos Gerados

A extração GraphQL gera os seguintes arquivos:

```
data/bronze/
├── commits_graphql_{repo_name}.json   # Commits por repositório
└── commits_graphql_all.json           # Todos os commits consolidados
```

## Query GraphQL Utilizada

```graphql
query($owner: String!, $name: String!, $branch: String!, $maxCommits: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: $branch) {
      target {
        ... on Commit {
          history(first: $maxCommits, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            totalCount
            nodes {
              oid
              message
              committedDate
              author {
                name
                email
                user { login databaseId }
              }
              committer {
                name
                email
                user { login databaseId }
              }
              additions
              deletions
              changedFiles
              parents(first: 1) {
                totalCount
              }
              tree { oid }
            }
          }
        }
      }
    }
  }
}
```

## Análises Possíveis

Com os dados de additions/deletions você pode criar:

### 1. Gráfico de Atividade de Código
- Linhas adicionadas vs removidas ao longo do tempo
- Identificar períodos de refatoração (muitas deleções)
- Identificar períodos de crescimento (muitas adições)

### 2. Análise de Produtividade
- Commits por desenvolvedor com volume de código
- Média de linhas alteradas por commit
- Identificar commits grandes vs pequenos

### 3. Análise de Qualidade
- Ratio additions/deletions
- Tamanho médio de commits por desenvolvedor
- Commits de merge vs commits normais

### 4. Heatmap de Mudanças
- Quais repositórios tem mais mudanças
- Quais períodos têm mais atividade
- Correlação entre número de commits e volume de código

## Exemplo de Uso na Camada Silver

```python
# silver_process.py
def process_commit_metrics(commits_data):
    """Processa métricas de commits"""

    metrics = {
        'total_commits': len(commits_data),
        'total_additions': sum(c['additions'] for c in commits_data),
        'total_deletions': sum(c['deletions'] for c in commits_data),
        'average_commit_size': sum(c['total_changes'] for c in commits_data) / len(commits_data),
        'merge_commits': sum(1 for c in commits_data if c['is_merge']),
    }

    return metrics
```

## Performance

### GraphQL vs REST

| Aspecto | GraphQL | REST API |
|---------|---------|----------|
| Requisições para 1000 commits | ~10 | 1000+ |
| Dados de additions/deletions | Sim | Não (precisa requisição extra) |
| Rate limit | Mais generoso | 5000/hora |
| Velocidade | Mais rápido | Mais lento |

## Troubleshooting

### Erro: "Branch not found"

Alguns repositórios usam `master` em vez de `main`:

```python
# A função automaticamente usa default_branch do repositório
# Se necessário, especifique manualmente no teste:
--branch master
```

### Erro: Rate Limit

```
GraphQL Rate limit: 0/5000
```

Aguarde o reset ou distribua a extração ao longo do tempo.

### Erro: Token sem permissões

Certifique-se de que seu token tem as seguintes permissões:
- `repo` (acesso completo a repositórios)
- `read:org` (ler dados da organização)

## Próximos Passos

1. Implementar extração GraphQL de commits
2. Criar processamento Silver para métricas de código
3. Implementar visualizações de análise de commits
4. Adicionar dashboard de produtividade

## Referências

- [GitHub GraphQL API Docs](https://docs.github.com/en/graphql)
- [GraphQL Explorer](https://docs.github.com/en/graphql/overview/explorer)
- [Rate Limiting GraphQL](https://docs.github.com/en/graphql/overview/resource-limitations)
