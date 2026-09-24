# CoOps — Collaboration & Ops Metrics for GitHub Organizations

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Beta-yellow.svg)](#status)
[![Backend tests](https://img.shields.io/badge/backend%20coverage-87%25-brightgreen.svg)](#running-tests)
[![Frontend tests](https://img.shields.io/badge/frontend%20coverage-91%25-brightgreen.svg)](#running-tests)
[![Contributions welcome](https://img.shields.io/badge/Contributions-Welcome-success)](CONTRIBUTING.md)

CoOps is an open-source full-stack dashboard for **continuous monitoring of
collaboration in GitHub-based academic software factories**. It combines
technical metrics (commits, pull requests, issues, code churn, lead time) and
collaboration metrics (review participation, contribution networks,
contribution distribution) across 13+ interactive D3.js pages, with optional
natural-language explanations powered by Google Gemini. Data is processed
through a [Medallion Architecture](ARCHITECTURE.md) (Bronze → Silver → Gold)
executed daily by GitHub Actions and published via GitHub Pages — no server
infrastructure required.

The dashboard was co-developed with Prof. Carla Rocha (UnB) and is in
production at two universities:

- **UnB (MDS course)** — <https://unb-mds.github.io/2025-2-Squad-01/>
- **UDF (LabTech factory)** — <https://labtechudf.github.io/CoOps/>

---

## Features

- **Technical metrics** — commits per author/repo/sprint, code churn, PR lead
  time, issue throughput and resolution time.
- **Collaboration metrics** — review participation, author↔reviewer network,
  contribution distribution (Gini), weekly regularity, ramp-up slope.
- **13+ interactive D3.js pages** — Treemap, CirclePack, Collaboration
  Network, Activity Heatmap, Timeline, Repository Fingerprint, KPI overviews,
  language composition.
- **Medallion ETL** — Bronze (7 extraction modules, append-only raw JSON),
  Silver (6 analytics modules with dim/fact lineage), Gold (executive KPIs).
- **Optional AI analysis** — natural-language summaries per member via Google
  Gemini; degrades gracefully when the API key is absent.
- **Serverless deployment** — daily ETL via GitHub Actions, dashboard hosted
  on GitHub Pages.

---

## Installation

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (it installs Python 3.10+ if needed)
- Node.js 20 or newer
- A GitHub Personal Access Token with `repo` and `read:org` scopes

### Backend (ETL pipeline)

```bash
git clone https://github.com/unb-mds/CoOps.git
cd CoOps
uv sync                # or: uv sync --no-dev   (runtime only)
```

Without uv, plain pip works too (deps come from `pyproject.toml`):

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\Activate.ps1
pip install -e . --group dev                          # pip >= 25.1; or: pip install -e .
```

Either way you get the `coops` package and the `coops-bronze`, `coops-silver`,
`coops-gold`, `coops-aggregate` and `coops-registry` console commands (prefix
them with `uv run` if you did not activate a virtualenv). CI runs
`uv sync --locked`.

Create a `.secrets` file at the repository root (already in `.gitignore`):

```env
GITHUB_TOKEN=ghp_your_token_here
GITHUB_ORG=your-github-organization
GEMINI_API_KEY=optional_gemini_key      # leave unset to disable AI analysis
```

### Frontend (dashboard)

```bash
cd dashboard
npm install
npm run dev                  # http://localhost:5173
```

---

## Quickstart

Run the pipeline against your organization (the GitHub Actions workflows run
the same commands daily). Credentials and org come from `.secrets`/env (see
above), not CLI flags. Prefix with `uv run` unless a virtualenv is
active:

```bash
uv run coops-bronze --cache
uv run coops-silver
uv run coops-gold
uv run coops-aggregate
uv run coops-registry
```

The commands write to `./data` and `./cache` in the current directory. From
the repository root they overwrite the tracked `data/*.json` registry files;
to keep your checkout clean, run them from another directory with
`uv run --project <path-to-CoOps> ...`.

Optional AI analysis step (requires `GEMINI_API_KEY`):

```bash
uv run python -m coops.ai_analysis.generate_members_ai
```

The dashboard reads the generated JSON files in `data/` and visualizes them at
`http://localhost:5173` (development) or your GitHub Pages URL (production).

---

## Running Tests

```bash
uv run pytest                            # backend; current coverage: 87%
cd dashboard && npm run test:coverage    # frontend; current coverage: 91%
```

CI runs both suites on every push and pull request — see
`.github/workflows/python-unit-tests.yaml`,
`.github/workflows/python-integration-tests.yaml`, and the frontend test
config under `dashboard/vitest.config.ts`.

---

## Documentation

- **Architecture deep-dive** — [ARCHITECTURE.md](ARCHITECTURE.md)
- **AI module** — [README_AI_ANALYSIS.md](README_AI_ANALYSIS.md)
- **Guide for AI coding agents** — [AGENTS.md](AGENTS.md) and [docs/](docs/)
- **Contributing & development workflow** — [CONTRIBUTING.md](CONTRIBUTING.md)
- **Testing a PR against a real organization** — [docs/TESTING_PULL_REQUESTS.md](docs/TESTING_PULL_REQUESTS.md)
- **Code of Conduct** — [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- **Security policy** — [SECURITY.md](SECURITY.md)
- **Release notes** — [CHANGELOG.md](CHANGELOG.md)

---

## Status

CoOps is in **Beta**. The pipeline runs in production on two university
deployments, the backend test coverage is 87% and the frontend is 91%, but the
public API surface (CLI flags, JSON schema of `data/silver/`,
`data/gold/`) may still evolve. See [CHANGELOG.md](CHANGELOG.md) for the
release history and [open issues](https://github.com/danrleypereira/CoOps/issues)
for the current roadmap.

---

## Citing CoOps

A Journal of Open Source Software submission is in preparation. Once the DOI
is assigned, please cite the JOSS paper. Until then, you may cite the
repository:

```bibtex
@software{coops2026,
  author  = {Pereira, Danrley Willyan da Silva and Rocha Aguiar, Carla Silva
             and Luz, Kerlla de Souza},
  title   = {CoOps: A full-stack dashboard for monitoring collaboration in
             GitHub-based academic software factories},
  year    = {2026},
  url     = {https://github.com/danrleypereira/CoOps},
  version = {v1.0.0}
}
```

---

## Acknowledgements

CoOps is developed under the PIBIT 2025–2026 scholarship program at the
**Centro Universitário do Distrito Federal (UDF)**, with co-development from
the **Universidade de Brasília (UnB)** MDS course (Prof. Carla Rocha) and the
LAPPIS research group at FGA/UnB. Inspired by GitHub-native insight panels,
SonarQube-style quality benchmarks, and academic software-factory experience
reports.

---

## License

Distributed under the **GNU General Public License v3.0 or later** — see
[LICENSE](LICENSE). By contributing, you agree that your contributions will be
licensed under the same terms.
