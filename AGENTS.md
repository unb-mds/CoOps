# AGENTS.md

CoOps is a GitHub-organization collaboration dashboard: a Python Medallion ETL
(Bronze extraction → Silver analytics → Gold KPIs) plus a React dashboard
published on GitHub Pages. This file is what an agent needs before its first
change; the details live in [`docs/`](docs/).

**Read for a code change** — these four, and stop:

| Topic | File |
|---|---|
| What "done" means for a change | [docs/definition-of-done.md](docs/definition-of-done.md) |
| Pipeline, package layout, data flow | [docs/architecture.md](docs/architecture.md) |
| Vocabulary and data shapes | [docs/domain.md](docs/domain.md) |
| Test suites and how to run them | [docs/testing.md](docs/testing.md) |

**Open only when the task is actually about them** — about 18k tokens combined
(measured 2026-09-23), nearly three times the injected set, and a code change
opens none of them:

| Topic | File |
|---|---|
| Setup, secrets, local pipeline runs | [docs/development.md](docs/development.md) |
| Development data: frozen raw, disposable derived | [docs/development-data.md](docs/development-data.md) |
| Running a phase: implement, verify, merge | [docs/phase-workflow.md](docs/phase-workflow.md) |
| Validating a PR with `gh act` | [docs/local-actions.md](docs/local-actions.md), [docs/TESTING_PULL_REQUESTS.md](docs/TESTING_PULL_REQUESTS.md) |
| The failure behind each DoD rule | [docs/dod-cases.md](docs/dod-cases.md) |
| Human-facing contribution rules | [CONTRIBUTING.md](CONTRIBUTING.md) (Portuguese) |

Read what the task needs. Injected context costs *steps × size*, because every
step re-sends the whole conversation — a document you open on step 3 is still
being paid for on step 80. Measured across 36 dispatched sessions: 889k tokens
of fresh input against 224.6M of cache reads, with the cache reads fitting
`≈ 1000 × steps²`. Reading four documents you did not need is not a small
constant; it is a constant multiplied by every step that follows.

## Five things that are easy to get wrong

1. **Dependencies are managed with uv**, locked in `uv.lock`: `uv sync`, then
   `uv run <cmd>`. There is no `requirements.txt`, no Poetry, no `pytest.ini`
   and no `.coveragerc` — pytest and coverage are configured in `pyproject.toml`.
2. **The package is `coops` under `src/`.** Import `from coops.utils.github_api
   import ...`; the ETL runs through the console commands (`coops-bronze`,
   `coops-silver`, `coops-gold`, `coops-aggregate`, `coops-registry`), not
   through file paths.
3. **The ETL takes no credentials on the command line.** `GITHUB_TOKEN` and
   `GITHUB_ORG` come from the environment, `.env` or `.secrets` through
   `coops.infrastructure.Settings`.
4. **GitHub Actions is disabled on `danrleypereira/CoOps` by design.** CI and
   the pipeline run in the organization fork `unb-mds/CoOps`; a PR gets no
   checks upstream. See [docs/testing.md](docs/testing.md).
5. **Everything under `data/` is pipeline output**, committed by bots in the
   fork. Don't hand-edit it, and don't commit what a local run leaves there.

## Commands

```bash
uv sync                      # install (Python >= 3.10)
uv run pytest                # backend suite
uv run coops-bronze --cache  # then coops-silver, coops-gold, coops-aggregate, coops-registry
cd dashboard && npm ci && npm run test:coverage   # frontend (Node 20 or 22)
```

The ETL reads and writes `./data` in the current directory. From the repository
root it overwrites tracked registry files, so run it from a scratch directory
with `uv run --project <repo> ...`.

## Conventions

- **Conventional Commits** (`feat:`, `fix:`, `docs:`, `ci:`, `test:`, …).
- **No `Co-Authored-By` trailers for AI assistants.** Human co-authors stay.
- Specialised agents live in `.opencode/agents/` (`reviewer`, `tester`,
  `architect`) — `opencode run --agent reviewer`. Don't let a reviewer inherit
  a cheap model: it writes confident summaries of work it didn't do.
- **A change is done when it meets [docs/definition-of-done.md](docs/definition-of-done.md)** —
  tests for the behaviour you changed, a suite you ran and quoted, lint/format/
  type-check green, and every new guard shown capable of failing.
- Update `CHANGELOG.md` under `[Unreleased]` for user-visible changes.
- **Never write `Closes #N` in a PR that targets a phase branch.** Work here
  merges issue PR → `phase/<epic>-<slug>` → `main`, and GitHub only honours the
  keyword on a merge into the *default* branch. Merging into a phase branch
  ignores it **and spends it**, so the issue never closes — not then, and not on
  the eventual merge to `main` either. Write `Refs #N` and close the issue by
  hand. This is how the tracker reached 84 open issues whose work was long since
  delivered.
- GPL-3.0-or-later.
- Open the PR as a draft, fill in `.github/PULL_REQUEST_TEMPLATE.md` (its
  **Organization validation** section is where the validation output goes),
  validate it locally with `gh act`, then mark it ready —
  [docs/TESTING_PULL_REQUESTS.md](docs/TESTING_PULL_REQUESTS.md) and
  [docs/local-actions.md](docs/local-actions.md).
