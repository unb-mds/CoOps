# Architecture

How the pipeline, the package and the dashboard fit together. For the rationale
behind the layering, see [ARCHITECTURE.md](../ARCHITECTURE.md); this file is the
map an agent needs to find things.

## Medallion pipeline

```
GitHub API ──► Bronze (raw JSON)  ──► Silver (analytics)  ──► Gold (KPIs)
               data/bronze/           data/silver/            data/gold/
```

| Layer | Command | Writes |
|---|---|---|
| Bronze | `coops-bronze` | `repositories_filtered.json`, `members_basic.json`, `members_detailed.json`, per-repository `issues_<repo>.json` / `prs_<repo>.json` / `commits_<repo>.json` / `issue_events_<repo>.json` / `repo_<repo>.json` / `structure_<repo>.json` (the `*_all.json` aggregates were removed in #170) |
| Silver | `coops-silver` | `members_analytics.json`, `contribution_metrics.json`, `collaboration_edges.json`, `temporal_events.json`, `activity_heatmap.json`, `repository_metrics.json`, `available_repos.json`, `language_analysis_all.json`, `hierarchy_<repo>.json`, … |
| Gold | `coops-gold` | `timeline_last_7_days.json`, `timeline_last_12_months.json` |
| Gold KPIs | `coops-aggregate` | `executive_dashboard.json`, `performance_tiers.json` |
| Registry | `coops-registry` | `data/master_registry.json`, `data/data_catalog.json` |

Each layer reads the previous layer's files from `./data`; nothing is passed in
memory between them. (The KPI step also reads
`data/bronze/members_detailed.json`, so its member count includes members
without a profile.) The AI step
(`python -m coops.ai_analysis.generate_members_ai`) is optional and writes
`data/silver/ai/members_ai.json`.

## Package layout

```
src/coops/
├── etl/              # console entry points: bronze_extract, silver_process,
│                     # gold_process, gold_aggregate, registry_manager
├── bronze/           # extractors: repositories, issues, commits, members,
│                     # repository_structure
├── silver/           # analytics: member_analytics, contribution_metrics,
│                     # collaboration_networks, temporal_analysis,
│                     # members_statistics, file_language_analysis, available_repos
├── gold/             # timeline_aggregation
├── ai_analysis/      # Gemini summaries (optional)
├── domain/           # tenancy value objects
├── infrastructure/   # Settings (pydantic-settings)
└── utils/            # github_api (REST + GraphQL client), data_helpers
```

`utils/github_api.py` is the only module that talks to GitHub. It owns caching
(`cache/`, keyed by URL hash), retries, rate-limit handling and pagination
(`get_paginated`). REST responses are cached as `<md5(url)>.json` with their
`ETag` in a sibling `<md5(url)>.etag` sidecar; a warm entry is revalidated with
`If-None-Match`, and a `304` serves the cached body without consuming a
rate-limit slot. The `cache/` corpus is deliberately **not** uploaded to GitHub
Actions cache: it is the unmodified API representation and contains personal
data, so it must never leave the machine (issue #107). A durable cross-run
cache belongs on infrastructure we control — a self-hosted runner, or the Mongo
raw layer (issue #113). Phase 2 (#26) splits it into transport, queries and an
adapter behind a port.

## Dashboard

React 19 + Vite + TypeScript + Tailwind + D3, tested with Vitest, in `dashboard/`.

All data loading goes through `dashboard/src/services/dataSource.ts`:

- default (`VITE_USE_LOCAL_DATA=false`): `https://raw.githubusercontent.com/<VITE_GITHUB_ORG>/<VITE_GITHUB_REPO>/main/data/<path>`;
- `VITE_USE_LOCAL_DATA=true`: `/data/<path>`, served from `dashboard/public/data/`.

A 404 means "the pipeline hasn't produced this file yet": `fetchData` raises
`DataNotFoundError` and pages render the shared `DataNotGenerated` state instead
of an error. Other failures stay errors.

GitHub Pages serves only the built dashboard — never `data/` — so any fetch that
bypasses `dataSource` would 404 in production. `deploy-pages.yaml` copies
`index.html` to `404.html` so deep links such as `/CoOps/ai` resolve.

## Workflow chain

```
bronze-extract.yaml ──(workflow_call)──► silver-process.yaml
      │                                        │
      │                                        ▼
      │                                 gold-process.yaml ──► AI analysis (if GEMINI_API_KEY)
      │                                        │
      │                                        ▼
      │                                 gold-aggregate.yaml   (executive KPIs)
      ▼
deploy-pages.yaml  ◄── workflow_run: "Bronze Layer - Data Extraction" | "Gold Layer - KPI Aggregation", branches: [main]
```

- Bronze runs on `push` to `main`, on a daily `0 5 * * *` schedule and on
  `workflow_dispatch` (with caps: `max_repos`, `max_issues`, `max_prs`,
  `max_commits_per_repo`, `since`, `skip_structure`). It has **no**
  `pull_request` trigger: every layer commits its JSON to the branch it runs on.
- Silver, Gold and the KPI aggregation are reusable workflows: when the chain
  *calls* them they run inside the Bronze run and emit no `workflow_run` event of
  their own, which is why the Pages deploy listens for the Bronze chain. They can
  also be dispatched manually, and a dispatched KPI run does emit an event —
  hence both names in the deploy's `workflow_run` filter.
- `validate-pipeline.yaml` is the PR-safe path: same chain, capped, committing
  nothing. See [testing.md](testing.md).
- `start.yaml` is deprecated.
