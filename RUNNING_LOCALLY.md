 # Running CoOps locally (extraction + frontend)

This guide covers the full local workflow: simulating the GitHub Actions with
`act`/`gh act` to generate the data (Bronze → Silver → Gold), and then
pointing the frontend (`dashboard/`) at that locally generated data instead
of fetching it from GitHub.

## 📋 Prerequisites

### 1. Install the GitHub CLI (gh)
```bash
sudo apt update
sudo apt install gh
```

### 2. Install act
```bash
gh extension install nektos/gh-act
gh act --version      # expect 0.2.89 or newer
```

> Install it as the `gh` extension, not with
> `curl .../install.sh | sudo bash`. Releases before 0.2.86 are affected by
> CVE-2026-34041 and CVE-2026-34042; if you already have an old
> `/usr/local/bin/act`, remove it (`sudo rm /usr/local/bin/act`). Keep the
> extension current with `gh extension upgrade nektos/gh-act`. Commands below
> written as `act ...` work the same as `gh act ...`.

### 3. Install uv and the dependencies
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv also downloads Python 3.10+ if needed
uv sync
```

### 4. Install Node.js (only needed for Part 2 — the frontend)
```bash
# Node >= 20 (see "engines" in dashboard/package.json)
```

## 🔑 Token setup

### 5. Generate a GitHub token (PAT)
- Go to https://github.com/settings/tokens
- Click "Generate new token (classic)"
- Grant the following scopes:
  - `repo` (full repository access)
  - `read:org` (read organization data)
  - `read:user` (read user data)
- Copy the generated token

### 6. Create the `.secrets` file at the repository root
```bash
# Copy the example file
cp EXAMPLE.secrets .secrets

# Edit with your real data
nano .secrets
```

Contents of `.secrets`:
```
GITHUB_TOKEN=ghp_your_real_token_here
GITHUB_ORG=coops-org
```

> `GITHUB_ORG` in `.secrets` controls which org is extracted when running `uv run coops-bronze` directly. `COOPS_ORG` / `COOPS_GITHUB_TOKEN` are accepted too and take precedence. It does **not** apply to `act`/`gh act`: `--secret-file` only fills `secrets.*`, and the workflows set `GITHUB_ORG` from the `COOPS_ORG` variable, so pass `--var COOPS_ORG=<org>` (as in the examples below), otherwise the owner of your `origin` remote is extracted. On GitHub Actions the workflows use the `COOPS_ORG` repository variable, falling back to the org that owns the repository (`github.repository_owner`): GitHub doesn't allow secret or variable names starting with `GITHUB_`.

### Organization token for GitHub Actions

Members are the organization's members plus everyone who contributed to the
extracted repositories. The default Actions token only sees **public**
memberships (none in `unb-mds`, whose members are all concealed), so without
a token the list has only the contributors, and a lower rate limit applies.
To include every organization member, add a token as the `COOPS_GITHUB_TOKEN`
repository secret; the Bronze and validation workflows use it for reading
GitHub and fall back to the default token when it's not set.

> With this token, concealed memberships are extracted and committed to the
> fork's public `main`, and the dashboard shows them.

1. Create a [fine-grained token](https://github.com/settings/personal-access-tokens/new):
   resource owner = the organization, repository access = **Public
   repositories** (anything the token can read may be committed publicly, so
   don't grant private repositories), organization permission
   **Members: Read-only**.
2. Paste it at the prompt of `gh secret set COOPS_GITHUB_TOKEN --repo <org>/CoOps`
   (never in a chat, issue or command line).
3. Fine-grained tokens expire: renew it and set the secret again before the
   expiration date. An expired token makes the Bronze workflow fail at
   "Verify organization is valid" with an HTTP 401.

> To validate a branch against a real organization on GitHub Actions without committing any data, use the **Validate Pipeline (manual)** workflow — see [docs/TESTING_PULL_REQUESTS.md](docs/TESTING_PULL_REQUESTS.md).

---

## Part 1 — Extracting the data (Bronze → Silver → Gold)

The system uses a layered architecture: Bronze → Silver → Gold.

### Option 1: Full pipeline (Bronze → Silver → Gold)
```bash
act workflow_dispatch -W .github/workflows/bronze-extract.yaml --secret-file .secrets --var COOPS_ORG=coops-org --bind
```

### Option 2: Individual layers

**Bronze layer (raw data extraction)**:
```bash
act workflow_dispatch -W .github/workflows/bronze-extract.yaml --secret-file .secrets --var COOPS_ORG=coops-org --bind -j extract-bronze-data
```

**Silver layer (analytics processing)**:
```bash
act workflow_dispatch -W .github/workflows/silver-process.yaml --secret-file .secrets --bind --container-options "--user $(id -u):$(id -g)" -j process-silver-data
```

**Gold layer (executive KPIs)**:
```bash
act workflow_dispatch -W .github/workflows/gold-process.yaml --secret-file .secrets --bind --container-options "--user $(id -u):$(id -g)" -j process-gold-data
```

> ℹ️ The pipeline chain is `bronze-extract.yaml` → `silver-process.yaml` → `gold-process.yaml` (timelines, optional AI analysis via `GEMINI_API_KEY`) → `gold-aggregate.yaml` (executive KPIs and performance tiers). When the chain succeeds on `main`, `deploy-pages.yaml` publishes the dashboard.

> ⚠️ **Silver and Gold require `--bind` (they won't produce anything without it)**: they consume the data the previous layer wrote, and their "Commit and push" steps are skipped under `act` (`if: ... && !env.ACT`), so nothing carries the data between layers. Without `--bind`, `act` gives each job a fresh copy of your checkout and the chained Silver job fails at `Verify Bronze data exists`. With `--bind` the layers share the real `data/` directory and the whole chain runs. Always include `--bind` (and `--container-options "--user $(id -u):$(id -g)"` to avoid `root:root`-owned generated files) for these workflows.

### Quick test (few commits, ideal for local debugging)

The Bronze workflow accepts `max_repos`, `max_commits_per_repo`, `skip_structure`, `since`, `max_issues` and `max_prs` as `workflow_dispatch` inputs, only used when triggered manually (push/schedule runs still extract everything). Use these to test quickly without waiting for a full organization extraction:
```bash
# Extracts only up to 3 repositories, 5 commits/issues/PRs per repository,
# skips structure extraction, and limits commit extraction to 2026 onward
gh act workflow_dispatch \
  -W .github/workflows/bronze-extract.yaml \
  -j extract-bronze-data \
  --secret-file .secrets \
  --var COOPS_ORG=coops-org \
  --bind \
  --container-options "--user $(id -u):$(id -g)" \
  --input max_repos=3 \
  --input max_commits_per_repo=5 \
  --input skip_structure=true \
  --input since=2026-01-01T00:00:00Z \
  --input max_issues=5 \
  --input max_prs=5
```
`max_repos`/`max_issues`/`max_prs` also cap the corresponding paginated search (not just a cut on what's saved — it speeds up the search itself). Since the blacklist/fork filter runs after the `max_repos` cut, the result may have fewer repositories than requested if the first ones in the list get filtered out. `max_issues`/`max_prs` share the same paginated call (issues and PRs come from the same GitHub API response), so pagination stops early only when both are set (at the larger of the two); setting just one never truncates the other. All caps must be positive integers. Issue events (`issue_events_*.json`) aren't affected by these caps.

This is the standard command for quick local extraction tests. It requires the `gh-act` extension (`gh extension install nektos/gh-act`) as an alternative to `act` installed via script (step 2 above) — both work, `gh act` just reuses the authentication already configured in `gh`.

> ⚠️ **Careful with `--bind`**: it mounts the real repository inside the container, and the container runs as `root` — any file created/modified (in `cache/`, `data/`, etc.) ends up owned by `root:root` on the host, and `data/` is tracked, so your checkout is left dirty with files you can't delete without `sudo`. Add `--container-options "--user $(id -u):$(id -g)"` to the command (runs the container with your UID/GID; if your `act` ignores that flag, use `sudo chown -R "$(id -u):$(id -g)" data cache .venv` as a fallback). Prefer committing or setting your work aside before running locally, as an extra safety net.
>
> Your working tree is **not** at risk from `actions/checkout@v4`'s `git clean -ffdx`: `act` never executes that action, it substitutes its own workspace-population step (a `docker cp` without `--bind`, a no-op with it). That is also why the checkout steps must **not** carry `if: ${{ !env.ACT }}` — skipping them leaves the container with an empty workspace and `uv sync` fails with `No pyproject.toml found`. See [docs/local-actions.md](docs/local-actions.md).

> ⚠️ With `--bind`, the workflows' `uv sync --locked` step also runs against your checkout and rewrites `.venv` for the container's Python. Afterwards, `uv run` on your machine rebuilds it (a few seconds). If it is owned by `root`, remove it first: `sudo rm -rf .venv`.

### Legacy workflow (old system — DEPRECATED)
```bash
act workflow_dispatch -W .github/workflows/start.yaml --secret-file .secrets --bind
```

### 🛠️ Manual execution (alternative to `act`)

If you'd rather run the scripts directly, without simulating Actions:
```bash
# 0. Install the package (once): uv sync

# 1. Bronze: data extraction (GITHUB_TOKEN/GITHUB_ORG come from .secrets or the environment)
uv run coops-bronze --cache

# 2. Silver: processing
uv run coops-silver

# 3. Gold: timelines, then executive KPIs and performance tiers
uv run coops-gold
uv run coops-aggregate

# 4. Registry: update the registry
uv run coops-registry
```

> The commands read and write `./data` and `./cache`. From the repository root they overwrite the tracked `data/master_registry.json` and `data/data_catalog.json` (and, in a production fork, the committed data); don't commit those changes from a local run. To keep your checkout clean, run them from a scratch directory with `uv run --project <repo> ...` (see [docs/TESTING_PULL_REQUESTS.md](docs/TESTING_PULL_REQUESTS.md#2-local-checks)).

### 📁 Generated data structure

After a successful run, you'll have (all under `data/`, at the project root):

**Bronze layer (raw data)** — `data/bronze/`:
`repositories_filtered.json`, `members_basic.json`, `members_detailed.json`, and one file per repository: `issues_<repo>.json`, `prs_<repo>.json`, `commits_<repo>.json`, `issue_events_<repo>.json`, `repo_<repo>.json`, `structure_<repo>.json`

**Silver layer (processed analytics)** — `data/silver/`:
`members_analytics.json`, `contribution_metrics.json`, `collaboration_edges.json`, `temporal_events.json`, `activity_heatmap.json`, `cycle_times.json`, `language_analysis_all.json`

**Gold layer (executive KPIs)** — `data/gold/`:
`executive_dashboard.json`, `performance_tiers.json`, `timeline_last_7_days.json`

### 🔍 Useful act parameters

- `--bind`: generated files show up on the host machine
- `--secret-file .secrets`: uses the local secrets file
- `--dry-run`: simulates without executing
- `--verbose`: detailed output for debugging
- `-j job-name`: runs a specific job
- `--pull=false`: skips pulling Docker images (faster)

### 📊 Verifying the results

```bash
# Bronze (lists; the first item may be a `_metadata` record)
ls -la data/bronze/
jq '[.[] | select(has("_metadata") | not) | .name]' data/bronze/repositories_filtered.json

# Silver
ls -la data/silver/
jq '[.[] | select(has("_metadata") | not)] | length' data/silver/contribution_metrics.json

# Gold
ls -la data/gold/
jq '.organization_health' data/gold/executive_dashboard.json

# Full registry
jq '.bronze | keys' data/master_registry.json
jq '.silver | keys' data/master_registry.json
```

---

## Part 2 — Pointing the frontend at local data

By default (`dashboard/.env.example`), the frontend fetches data from
`https://raw.githubusercontent.com/{VITE_GITHUB_ORG}/{VITE_GITHUB_REPO}/main/data`
— i.e. from the repository's `main` branch on GitHub, not the `data/` you
just generated locally in Part 1. That's intentional (it's the same
behavior as the production deploy, in
`.github/workflows/deploy-pages.yaml`, which always runs with
`VITE_USE_LOCAL_DATA: false`). To make the frontend see your local
extraction, two steps:

### 1. Enable local data mode

In `dashboard/.env` (create it from `dashboard/.env.example` if it doesn't exist yet):
```env
VITE_USE_LOCAL_DATA=true
```
This makes `dashboard/src/services/dataSource.ts` swap the base URL from
`https://raw.githubusercontent.com/...` to `/data` — which Vite serves from
`dashboard/public/data/`.

### 2. Expose the data under `dashboard/public/data/`

`public/data/` is deliberately git-ignored (`dashboard/.gitignore`) — data
should never be committed to the frontend. You need to put it there
manually after each extraction.

**Recommended — symlink (stays up to date after every new extraction):**
```bash
# from the repo root
mkdir -p dashboard/public
ln -s ../../data dashboard/public/data
```
Re-running Part 1 (bronze/silver/gold) makes the new files show up for the
frontend automatically, no need to repeat this step.

**Alternative — manual copy (needs repeating after every new extraction):**
```bash
mkdir -p dashboard/public/data
cp -r data/. dashboard/public/data/
```

> ⚠️ Note that `dashboard/src/pages/VisualizationUtils.ts` (used by the
> Structure/languages page) builds the URL as `${BASE_URL}data/...` directly,
> without going through `VITE_USE_LOCAL_DATA` — so that page depends on
> `dashboard/public/data/` existing regardless of the flag. The symlink
> above covers both cases.

> ℹ️ `available_repos.json` (fetched at the root, outside `data/`, by
> `RepositoryFilter.tsx`/`RepositoryToolbar.tsx`) isn't generated by any
> backend workflow today — the fetch fails silently (just a
> `console.warn`) if the file doesn't exist, so it doesn't block the rest
> of the dashboard.

---

## Part 3 — Starting the frontend

```bash
cd dashboard
npm install
npm run dev
```

Open `http://localhost:5173`. With `VITE_USE_LOCAL_DATA=true` and the
symlink/copy from Part 2 in place, the pages (Analytics, Organization,
Structure, etc.) should load the data you just extracted locally, instead
of the data published on GitHub's `main`.

To go back to testing against real GitHub data (no local data), just set
`VITE_USE_LOCAL_DATA=false` (or remove the line from `.env`) and restart
`npm run dev`.

---

## 🐛 Troubleshooting

### Common issues (extraction):

1. **❌ Failed to fetch members**
   - **Cause**: the organization may have private members, or the token has limited permissions
   - **Fix**: the system has a smart fallback that discovers active contributors
   - **Recommended token scope**: `read:org` for public members
   - **Fallback**: discovers collaborators via the repositories' contributors API
   - **Result**: works even for organizations with private members

2. **❌ Error: 'name' (KeyError)**
   - **Cause**: JSON structure with unexpected metadata
   - **Fix**: scripts now handle metadata automatically
   - **Check**: whether `repositories_filtered.json` exists and is valid

3. **❌ API 403 Forbidden**
   - **Cause**: token without the right permissions, or rate limit
   - **Fix**: check the token's scopes:
     - `repo` (repository access)
     - `read:org` (organization data)
     - `read:user` (user profiles)

4. **❌ Empty data files**
   - **Cause**: organization with no public data
   - **Fix**: the system creates empty files to keep the structure consistent
   - **Normal**: for organizations with little public data

5. **❌ Rate limit exceeded**
   - **Fix**: wait for the reset, or use `--cache` to avoid re-downloading
   - **Check**: response headers show when the rate limit resets

6. **❌ Python dependencies**
   - **Fix**: `uv sync`
   - **uv not installed**: `curl -LsSf https://astral.sh/uv/install.sh | sh` (see https://docs.astral.sh/uv/getting-started/installation/)

7. **❌ `Verify Bronze/Silver data exists` fails when running Silver/Gold**
   - **Cause**: ran the layer without `--bind`. `act` gives each job a fresh copy of your checkout, and the "Commit and push" steps that would carry the data forward are skipped under `act`
   - **Fix**: always include `--bind` (and `--container-options "--user $(id -u):$(id -g)"`) when running these workflows via `act`/`gh act`, as in the "Individual layers" example above

10. **❌ `fatal: not a git repository: (null)`**
   - **Cause**: you are running `act` from a **git worktree**. A worktree's `.git` is a file pointing at the main checkout, and that path does not exist inside the container, so every `git` call in a step dies
   - **Fix**: run `act` from a normal clone

11. **❌ `ReferenceError: File is not defined` in `astral-sh/setup-uv`**
   - **Cause**: stale runner image. `setup-uv@v10` declares `runs.using: node24`, and an old `catthehacker/ubuntu:act-latest` only carries Node 16/18/20
   - **Fix**: `docker pull catthehacker/ubuntu:act-latest`

### Common issues (frontend):

8. **❌ Dashboard still shows GitHub data, not local data**
   - **Cause**: `VITE_USE_LOCAL_DATA` isn't `true`, or the dev server was started before creating/editing `.env`
   - **Fix**: check `dashboard/.env`, then stop and re-run `npm run dev` (Vite only reads `.env` on startup)

9. **❌ 404 fetching `/data/silver/...json` in the browser**
   - **Cause**: `dashboard/public/data/` doesn't exist, or doesn't have the files for that layer
   - **Fix**: check whether Part 1 ran through the expected layer (bronze/silver/gold), and whether the symlink/copy from Part 2 was done from the repo root (`ln -s ../../data dashboard/public/data`)

### Verbose logs:
```bash
# Verbose run
act --verbose workflow_dispatch -W .github/workflows/bronze-extract.yaml --secret-file .secrets

# Check the API cache
ls -la cache/

# Check token permissions
curl -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user
```

### Step-by-step verification:

```bash
# 1. Test the token
curl -H "Authorization: Bearer $GITHUB_TOKEN" \
   https://api.github.com/orgs/coops-org

# 2. Test repositories
curl -H "Authorization: Bearer $GITHUB_TOKEN" \
   https://api.github.com/orgs/coops-org/repos | jq length

# 3. Test members (may fail if private)
curl -H "Authorization: Bearer $GITHUB_TOKEN" \
   https://api.github.com/orgs/coops-org/members | jq length

# 4. Run an individual step (token/org come from .secrets or the environment)
GITHUB_ORG=coops-org uv run coops-bronze
```

## 📈 Next steps

1. **Run the Bronze → Silver → Gold pipeline** (Part 1) to collect and process the data
2. **Enable `VITE_USE_LOCAL_DATA` and link `data/`** (Part 2) so the frontend can see the result
3. **Start the dashboard** (Part 3) and explore the pages with your own data
4. **Customize metrics** by editing the Silver/Gold scripts, and repeat the cycle
