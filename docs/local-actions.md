# Running the GitHub Actions workflows locally with `act`

Every workflow in `.github/workflows/` can be exercised on your own machine
with [`act`](https://github.com/nektos/act), which runs each job in a Docker
container that imitates a GitHub-hosted runner. This is the day-to-day way to
validate a change: you no longer need push access to a fork to see whether CI
would go green.

This guide is about **validating the workflows themselves**. For generating a
local dataset to point the dashboard at, see
[RUNNING_LOCALLY.md](../RUNNING_LOCALLY.md).

Everything below was run and verified on Docker 29.6.1 with
`gh act` 0.2.89 and `catthehacker/ubuntu:act-latest` (image built 2026-08-15).

## Prerequisites

### Docker

A working Docker daemon that your user can talk to (`docker ps` must succeed
without `sudo`). Roughly 6 GB of free disk: the runner image is ~1.5 GB and
each run adds container layers.

### `act`, via the `gh` extension

```bash
gh extension install nektos/gh-act
gh act --version      # expect 0.2.89 or newer
```

> **Do not use a system-wide `act` binary from before 0.2.86.** Some machines
> have an old `/usr/local/bin/act` (0.2.81) installed by the
> `curl … install.sh | sudo bash` line in RUNNING_LOCALLY.md. That version is
> affected by CVE-2026-34041 and CVE-2026-34042. Check with
> `act --version`, and if it is old, remove it (`sudo rm /usr/local/bin/act`)
> and use `gh act` instead. `gh act` keeps itself updateable through
> `gh extension upgrade nektos/gh-act`.

### The runner image

```bash
docker pull catthehacker/ubuntu:act-latest
```

**Pull it before your first run, and re-pull it if actions start failing in
odd ways.** A stale image is the single most likely cause of confusing
errors — see [Troubleshooting](#troubleshooting).

## Defaults: `.actrc`

The repository ships an [`.actrc`](../.actrc) that `act` reads automatically
when you run it from the repository root. It sets:

| Flag | Why |
|---|---|
| `-P ubuntu-latest=catthehacker/ubuntu:act-latest` (and `ubuntu-24.04`, `ubuntu-22.04`) | `act`'s own default image is a minimal one without Python, Node or the act tool cache. |
| `--artifact-server-path .act/artifacts` | `actions/upload-artifact@v4` talks to a local artifact server; without a path the upload steps fail. Uploads land in `.act/artifacts/` (gitignored). |
| `--container-daemon-socket -` | No workflow here builds images, so the runner does not get the host Docker socket. |

Useful flags to add on the command line:

- `--pull=false` — skip the registry check for the runner image (offline, or
  when you know it is current).
- `-j <job-id>` — run a single job. `act` then ignores that job's `needs:`,
  which is handy for iterating.
- `--verbose` — full `act` debug output.

## Secrets and variables

**Never put a real token in a file that can be committed.** Two safe ways:

```bash
# 1. On the command line, from the gh CLI's own credential store (preferred):
gh act … --secret COOPS_GITHUB_TOKEN="$(gh auth token)"

# 2. From a .secrets file, which .gitignore already excludes:
gh act … --secret-file .secrets
```

`act` also reads `.secrets` from the repository root automatically when the
file exists. `.gitignore` covers `.secrets` and `.vars`.

Repository *variables* (`vars.*`) are separate from secrets and are passed
with `--var`:

```bash
gh act … --var COOPS_ORG=unb-mds
```

`GITHUB_ORG` in `.secrets` does **not** reach the workflows — they read the
organization from `vars.COOPS_ORG`, falling back to the owner of your `origin`
remote. Always pass `--var COOPS_ORG=<org>` for the pipeline workflows.

`workflow_dispatch` inputs are passed with `--input name=value`.

## The workflows

### `python-unit-tests.yaml` — Unit Tests (Python + Frontend)

Needs nothing: no token, no network beyond package downloads.

```bash
# everything: 2 Python jobs, 2 frontend jobs, and the summary job
gh act workflow_dispatch -W .github/workflows/python-unit-tests.yaml

# just one side while iterating
gh act workflow_dispatch -W .github/workflows/python-unit-tests.yaml -j python-unit-tests
gh act workflow_dispatch -W .github/workflows/python-unit-tests.yaml -j frontend-unit-tests
```

Verified green — all five jobs:

```
[Unit Tests (Python + Frontend)/Python Tests (Python 3.10)-1] 🏁  Job succeeded
[Unit Tests (Python + Frontend)/Python Tests (Python 3.11)-2] 🏁  Job succeeded
[Unit Tests (Python + Frontend)/Frontend Tests (Node 22)-2  ] 🏁  Job succeeded
[Unit Tests (Python + Frontend)/Frontend Tests (Node 20)-1  ] 🏁  Job succeeded
[Unit Tests (Python + Frontend)/Test Summary                ] 🏁  Job succeeded
```

Measured runtimes (warm Docker layers and `~/.cache/act`): **113 s** for the
whole workflow, **23 s** for `-j python-unit-tests` alone, **89 s** for
`-j frontend-unit-tests` alone. The first run is several minutes longer
because it pulls the image and clones each action.

The `Comment … coverage on PR` steps are skipped locally: their `if:` requires
`github.event_name == 'pull_request'`.

### `python-integration-tests.yaml` — Python Integration Tests

```bash
gh act workflow_dispatch -W .github/workflows/python-integration-tests.yaml
```

Runs the suite on Python 3.10, 3.11 and 3.12. Verified green in a normal
clone — **27 s** with warm caches:

```
[Python Integration Tests/integration-tests-1] 🏁  Job succeeded
[Python Integration Tests/integration-tests-2] 🏁  Job succeeded
[Python Integration Tests/integration-tests-3] 🏁  Job succeeded
```

`codecov/codecov-action@v4` runs and its upload is rejected
(`Token required because branch is protected`), but the step still succeeds
because the workflow sets `fail_ci_if_error: false`.

> If you work in a **git worktree**, this workflow fails — see
> [Troubleshooting](#a-step-fails-with-fatal-not-a-git-repository-null).

### `validate-pipeline.yaml` — Validate Pipeline (manual)

The end-to-end check: the test suite, then a capped Bronze → Silver → Gold →
Aggregate → Registry run against a real organization, then a **Check outputs**
step that asserts every file the dashboard reads was produced. Nothing is
committed or pushed.

```bash
gh act workflow_dispatch -W .github/workflows/validate-pipeline.yaml \
  --secret COOPS_GITHUB_TOKEN="$(gh auth token)" \
  --var COOPS_ORG=unb-mds \
  --input max_repos=2 \
  --input max_issues=10 \
  --input max_prs=10 \
  --input max_commits_per_repo=10
```

Verified green. The run prints the same summary table as on GitHub:

```
### Pipeline validation: `unb-mds`

| File | Status | Records |
|---|---|---|
| `bronze/repositories_filtered.json` | ok | 2 |
| `bronze/issues_2024-example.json` | ok | 18 |
| `bronze/commits_2024-example.json` | ok | 65 |
…
| `data_catalog.json` | ok | object |
🏁  Job succeeded
```

Runtime with those caps: **86 s** measured end to end (both jobs). Bronze
extraction is the bulk of it (~47 s for two repositories); raise the caps and
it grows roughly linearly. Drop `--input max_repos` entirely for a full
extraction — minutes to hours, depending on the organization.

Notes:

- `-j pipeline` skips the `tests` job when you only care about the pipeline.
- The token needs no special scopes for a public organization; `gh auth token`
  is enough. A fine-grained `COOPS_GITHUB_TOKEN` with **Members: Read-only**
  additionally lists concealed organization members.
- Because `secrets.COOPS_GITHUB_TOKEN` is set, the workflow takes its
  "don't upload possibly-private data" branch and prints
  `::notice::COOPS_GITHUB_TOKEN is set, so the generated data is not uploaded.`
  instead of uploading the artifact. Run without `--secret` (and with a
  public org) to exercise the upload path.
- `--input run_ai=true` additionally needs `--secret GEMINI_API_KEY=…`.
- The generated `data/` stays inside the container: `act` copies the workspace
  in, so your checkout is not touched. Add `--bind` if you want the data on the
  host — and read the warning in the next section first.

### The layer workflows: `bronze-extract.yaml`, `silver-process.yaml`, `gold-process.yaml`, `gold-aggregate.yaml`

These are the production pipeline. On GitHub they **commit data to the branch
they run on** and chain to each other through `workflow_call`. Locally they
are useful for generating a dataset, not for validating a PR — use
`validate-pipeline.yaml` for that.

Each job on its own works with the workspace copy:

```bash
gh act workflow_dispatch -W .github/workflows/bronze-extract.yaml \
  -j extract-bronze-data \
  --secret COOPS_GITHUB_TOKEN="$(gh auth token)" \
  --var COOPS_ORG=unb-mds \
  --input max_repos=1 --input max_issues=5 --input max_prs=5 \
  --input max_commits_per_repo=5 --input skip_structure=true
```

**The chain only works with `--bind`.** `act` gives every job a fresh copy of
your workspace, and the "Commit and push" steps are skipped under `act`
(`if: … && !env.ACT`), so Bronze's output never reaches Silver. Without
`--bind` the chained Silver job fails at `Verify Bronze data exists`. With
`--bind`, `act` mounts your checkout instead of copying it, the layers share
`data/`, and the whole chain runs:

```bash
gh act --bind workflow_dispatch -W .github/workflows/bronze-extract.yaml \
  --secret COOPS_GITHUB_TOKEN="$(gh auth token)" \
  --var COOPS_ORG=unb-mds \
  --input max_repos=1 --input max_issues=5 --input max_prs=5 \
  --input max_commits_per_repo=5 --input skip_structure=true
```

Verified green, all four workflows in the chain:

```
[Bronze Layer - Data Extraction/extract-bronze-data] 🏁  Job succeeded
[trigger-silver/Silver Layer - Data Processing/process-silver-data] 🏁  Job succeeded
[trigger-gold-processing/Gold Layer - Data Aggregation/process-gold-data] 🏁  Job succeeded
[trigger-gold-aggregate/Gold Layer - KPI Aggregation/aggregate-gold-data] 🏁  Job succeeded
```

> ⚠️ **`--bind` writes into your working tree as `root`.** `data/`, `cache/`
> and `.venv/` end up owned by `root:root`, and `data/` is tracked, so your
> checkout is left dirty with files you cannot delete without `sudo`. Add
> `--container-options "--user $(id -u):$(id -g)"` to keep your own ownership,
> or clean up afterwards with
> `sudo chown -R "$(id -u):$(id -g)" data cache .venv`. Commit or set aside
> your work before a `--bind` run.

> `gold-aggregate.yaml` starts with a `Pull latest changes` step that runs
> `git pull origin "$(git rev-parse --abbrev-ref HEAD)"` with no fallback. It
> needs a reachable `origin` that already has your current branch; on a local
> branch that was never pushed, this step fails. Check out `main`, or push the
> branch, before running this workflow (or the full chain) locally.

### `deploy-pages.yaml` — builds locally, cannot deploy

Its main trigger is `workflow_run`, which `act` cannot synthesise, and its
last step publishes to the real GitHub Pages service. Dispatch it manually and
**everything up to the deploy succeeds**, which is enough to catch a broken
dashboard build:

```bash
gh act workflow_dispatch -W .github/workflows/deploy-pages.yaml
```

```
✅  Success - Main Install dependencies
✅  Success - Main Build dashboard with dynamic base path
✅  Success - Main Add .nojekyll file
✅  Success - Main Add SPA fallback
✅  Success - Main Upload artifact
❌  Failure - Main Deploy to GitHub Pages
    Error message: Unable to get ACTIONS_ID_TOKEN_REQUEST_URL env variable
```

`actions/deploy-pages@v4` authenticates with a GitHub OIDC token, and there is
no OIDC endpoint outside GitHub's runners. There is no workaround: that final
failure is expected and is not something to fix. To check only the build, run
`cd dashboard && npm ci && npm run build:gh` directly.

### `start.yaml` — deprecated, and broken

Its second job points a step's `uses:` at a workflow file as if it were an
action, which is not valid:

```
🏁  Job succeeded            # redirect-to-new-pipeline (just echoes)
Error: Unable to determine how to run job:trigger-new-pipeline
       step:Trigger new Bronze extraction pipeline
```

The same construct is rejected by GitHub. Nothing to run locally; the file is
deprecated.

## Troubleshooting

### `ReferenceError: File is not defined` in `astral-sh/setup-uv`

```
/opt/acttoolcache/node/18.20.8/x64/bin/node
…
ReferenceError: File is not defined
```

Your runner image is stale. `astral-sh/setup-uv@v10.1.0` declares
`runs.using: node24`; `act` runs it with the newest Node in the image's act
tool cache, and an image from 2025 only carries Node 16/18/20, so the action
falls back to Node 18, whose `undici` lacks the global `File`.

Fix:

```bash
docker pull catthehacker/ubuntu:act-latest
```

Confirm the image now has Node 24:

```bash
docker run --rm --entrypoint ls catthehacker/ubuntu:act-latest /opt/acttoolcache/node
# 20.20.2
# 24.19.0
```

This is also why `--pull=false` is not in `.actrc`: leaving the default pull
on keeps the image current. Only add `--pull=false` deliberately.

### `error: No pyproject.toml found in current directory or any parent directory`

The job's `Checkout repository` step was skipped, so the container's workspace
is empty. `act` does not execute `actions/checkout` — it *implements* it as
the `docker cp` that populates the workspace:

```
🐳  docker cp src=/path/to/repo/. dst=/path/to/repo
✅  Success - Main Checkout repository
```

So a checkout step guarded with `if: ${{ !env.ACT }}` leaves the job with
nothing to run against. The guard was removed from `bronze-extract.yaml`,
`silver-process.yaml` and `gold-process.yaml` for this reason.

The guard on the *Commit and push* steps is correct and stays: those must not
run locally. Removing the checkout guard is what makes that matter — before it,
an unguarded push step failed harmlessly in an empty container; afterwards the
job has a real checkout and a real `origin`, so an unguarded push would reach
the actual repository. `gold-process.yaml` had exactly that hole: two push
steps, one guard. Both are guarded now. **If you add a step that writes to a
remote, guard it** — and check with `grep -c 'git push'` against
`grep -c 'env.ACT'` per workflow, which is how the missing one was found.

### A step fails with `fatal: not a git repository: (null)`

You are running `act` from a **git worktree**. A worktree's `.git` is a file
containing `gitdir: /path/to/main/checkout/.git/worktrees/<name>`; `act`
copies that file into the container, where the target path does not exist, so
every `git` invocation dies:

```
[command]/usr/bin/git config --global --add safe.directory /path/to/worktree
fatal: not a git repository: (null)
Error: The process '/usr/bin/git' failed with exit code 128
```

This hits `codecov/codecov-action@v4` in `python-integration-tests.yaml` and
the `Pull latest …` steps of the layer workflows. Reproduced and confirmed:
the same `git config --global` succeeds in the same image when `.git` is a
real directory, or absent entirely.

Fix: run `act` from a normal clone rather than a worktree.

### `actions/upload-artifact@v4` fails or hangs

It needs the local artifact server. `.actrc` sets
`--artifact-server-path .act/artifacts`; if you run `act` from somewhere other
than the repository root, `.actrc` is not read and you must pass the flag
yourself.

### `gh: command not found` inside a step

The `catthehacker/ubuntu` "medium" images do not ship the GitHub CLI, although
GitHub-hosted runners do. `curl` and `jq` are present. Workflow steps in this
repository therefore use `curl` against `https://api.github.com` rather than
`gh api`, which behaves identically in both environments. Keep it that way
when adding steps.

### The run is slow every time

Each `act` run starts a fresh container, so `uv sync` and `npm ci` redownload.
`actions/setup-node`'s and `setup-uv`'s caches are written under `~/.cache/act`
and are reused across runs, which is most of the win. Beyond that, use
`-j <job>` to run a single job and `--input max_repos=1` to keep extractions
small.

### Cleaning up

```bash
rm -rf .act/artifacts        # local artifact uploads
docker image prune           # stopped containers are removed by act itself
```
