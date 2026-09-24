# Development

Setup, configuration and local runs. The long-form walkthrough (including
`act`) is [RUNNING_LOCALLY.md](../RUNNING_LOCALLY.md).

## Setup

```bash
uv sync                    # creates .venv and installs the project + dev group
uv run pytest              # verify
cd dashboard && npm ci     # Node 20 or 22
```

Python ≥ 3.10; uv downloads an interpreter if needed. Without uv:
`pip install -e . --group dev` (pip ≥ 25.1).

## Configuration

`coops.infrastructure.Settings` (pydantic-settings) reads, in this order of
precedence: environment variables, then `.secrets`, then `.env` — the later
file in `env_file=(".env", ".secrets")` wins. Copy `EXAMPLE.secrets`; both
files are git-ignored.

| Setting | Names accepted | Notes |
|---|---|---|
| GitHub token | `COOPS_GITHUB_TOKEN`, `GITHUB_TOKEN` | needed by Bronze only |
| Organization | `COOPS_ORG`, `GITHUB_ORG` | the org to extract |
| Gemini key | `GEMINI_API_KEY`, `GOOGLE_API_KEY` | optional; AI step is skipped without it |
| Gemini model | `GEMINI_MODEL` | defaults to `gemini-3.5-flash-lite` |
| MongoDB URI | `MONGO_URI` | local dev value `mongodb://localhost:27018`; when set, Bronze reads/writes the raw layer (see [Local MongoDB](#local-mongodb-development)) |
| Raw max age | `RAW_MAX_AGE_SECONDS` | how long a raw document stays "fresh" before Bronze re-fetches (seconds; default `3600`) |

Rules worth knowing:

- The `COOPS_` name wins over the `GITHUB_` one **within the same source**;
  the environment always beats the files. GitHub refuses to store secrets or
  variables whose names start with `GITHUB_`, which is why the aliases exist.
- **An empty value counts as unset**, because an undefined Actions secret
  expands to `""`.
- Silver, Gold and the registry need no credentials at all.

In Actions, Bronze reads `secrets.COOPS_GITHUB_TOKEN` when present (a
fine-grained token, public repositories, organization *Members: Read-only*) and
falls back to the default token, which only sees public memberships. Pushing
data always uses the default token.

## Running the pipeline locally

The commands read and write `./data` and `./cache` in the current directory, so
run them from a scratch directory to keep the checkout clean:

```bash
REPO=$PWD
cd "$(mktemp -d)"
export GITHUB_TOKEN=$(gh auth token) GITHUB_ORG=unb-mds
uv run --project "$REPO" coops-bronze --max-repos 3 --max-issues 20 --max-prs 20 \
  --max-commits-per-repo 50 --skip-structure
uv run --project "$REPO" coops-silver
uv run --project "$REPO" coops-gold
uv run --project "$REPO" coops-aggregate
uv run --project "$REPO" coops-registry
```

Cap flags must be positive integers. `--max-issues` and `--max-prs` cap what is
kept per repository; pagination stops early only when **both** are set, so
capping one never truncates the other. `--max-repos` bounds how many pages of
the repository list are fetched, and the fork/blacklist filter then runs before
the cap is applied — so a cap can yield fewer repositories than requested.
`--skip-structure` skips repository trees, which is the slow part.

## Reusing the corpus across worktrees

A full extraction is ~1.4 GB of raw API responses in `cache/` plus ~200 MB of
derived JSON in `data/`, and takes about an hour of rate-limited calls. Re-fetch
in every new worktree is not worth it: snapshot the corpus once and restore it
with `scripts/data-snapshot.sh`.

```bash
scripts/data-snapshot.sh pack          # from the worktree that has cache/ + data/
scripts/data-snapshot.sh list          # what snapshots exist, with size and age
scripts/data-snapshot.sh verify <snap> # check integrity without extracting
scripts/data-snapshot.sh unpack        # restore the newest into the current worktree
scripts/data-snapshot.sh unpack --into ../other-worktree
```

Snapshots are compressed tarballs kept in `$COOPS_SNAPSHOT_DIR` (default
`~/.local/share/coops/snapshots`), outside any worktree, so every worktree on
the machine shares one copy. `pack` writes a SHA-256 checksum next to each
archive; `unpack` verifies it first and refuses a corrupt archive, and refuses
to overwrite a non-empty `cache/` or `data/` unless you pass `--force`. Neither
`cache/` nor `data/` is committed to git — fetch once, `pack`, then `unpack`
into each worktree instead of re-fetching.

The corpus contains personal data — raw API responses include user email
addresses — so a snapshot must not be published or shared. `pack` keeps it
private at rest: the snapshot directory is mode 700 and the archive and its
checksum are mode 600.

### Two corpus artifacts (raw vs fixtures)

The managed corpus is captured in **two artifacts, never one** (issue #109):

| Artifact | What it is | Safe to share? |
|---|---|---|
| `corpus-raw` | Full, unmodified API payloads in the capture shape `{tenant_id, provider, endpoint, params, etag, fetched_at, payload}`. | **No.** Private — local or Mongo only, never a release asset, CI cache, issue or PR attachment. |
| `corpus-fixtures` | The same shape with personal data removed (email, location, bio, company, blog, hireable, twitter_username; email addresses in free text redacted). | **Yes.** This is the one to publish or copy into other repositories. |

`corpus-fixtures` is **safe to share**; `corpus-raw` is **not**.

Capture the raw corpus during extraction with `--capture-dir` (the directory
is tenant-scoped and written mode `700`/`600`):

```bash
uv run coops-bronze --capture-dir corpus-raw --max-repos 3 --skip-structure
```

Then snapshot and restore each artifact:

```bash
scripts/data-snapshot.sh pack-raw        # corpus-raw/  -> corpus-raw-*.tar.gz (private)
scripts/data-snapshot.sh unpack-raw      # restore the newest corpus-raw
scripts/data-snapshot.sh pack-fixtures   # corpus-raw/ -> sanitize -> corpus-fixtures-*.tar.gz
scripts/data-snapshot.sh unpack-fixtures # restore the newest corpus-fixtures
```

`pack-fixtures` runs `coops-corpus sanitize` (the same command, run directly:
`coops-corpus sanitize --raw corpus-raw --out corpus-fixtures`), so it needs a
synced project (`uv sync`).

**Retention.** `corpus-raw` has a stated retention policy: records whose
`fetched_at` is older than the policy are pruned. Enforce it with
`coops-corpus prune --raw corpus-raw --max-age-days N`, or prune-before-pack
with `scripts/data-snapshot.sh pack-raw --retention-days N`. There is no
default — set the policy you want (30 days is the suggested floor for a corpus
rebuilt on every run).

## Local MongoDB (development)

`docker-compose.dev.yml` provides a local MongoDB for work against a real
database. Silver and Gold still read and write `./data` — only Bronze talks to
MongoDB, capturing responses into a raw layer and reading them back instead of
re-fetching the API when they are fresh (see the `MONGO_URI` note below) — but
the stack gives the raw-capture storage work (#43, #107) a database to build
against without anyone having to install or host MongoDB.

```bash
make mongo-up        # docker compose -f docker-compose.dev.yml up -d
make mongo-down      # stop, keep data
make mongo-reset     # stop and delete the data volume
make mongo-logs      # follow logs
make mongo-load      # load the sanitized data/ tree into MongoDB
```

The image is pinned to an exact version (`mongo:7.0.14`, no floating tags) so
the integration suite can reuse the exact same image, and published on a
non-default host port so it never collides with a MongoDB a developer already
runs.

**Port.** MongoDB binds to loopback only — `127.0.0.1:${MONGO_PORT:-27018}`
(container port 27017) — so it listens on `localhost` and is **not** reachable
from the local network. The default `27018` deliberately avoids the standard
`27017`. Override it from `.env`:

```bash
cp .env.example .env        # then adjust MONGO_PORT if you need another port
MONGO_PORT=27019 make mongo-up
```

**Connection string.** `MONGO_URI` is read through
`coops.infrastructure.Settings` (the `mongo_uri` field). When it is set, Bronze
captures every response it fetches into the raw layer and reads a fresh raw
document back instead of re-fetching the API (a document is "fresh" for
`RAW_MAX_AGE_SECONDS`, default 3600). For the local stack use
`mongodb://localhost:27018` (change the port to match `MONGO_PORT`). It is
already in `.env.example`, and can live in `.env` or `.secrets`.

**Persistence and reset.** Data lives in a named volume, so it survives
`docker compose down` / `make mongo-down`. To start from scratch, delete the
volume with `make mongo-reset` (or `docker compose -f docker-compose.dev.yml down
-v`).

**Authentication.** The stack runs without authentication by default. That is
safe only because the port binds to loopback (see **Port.** above); anyone who
could reach the port already has a shell on the machine. To enable root auth,
set `MONGO_INITDB_ROOT_USERNAME` and `MONGO_INITDB_ROOT_PASSWORD` in
`.env` — which is git-ignored; `.env.example` is the committed template — and
use the same credentials in `MONGO_URI`
(`mongodb://<user>:<password>@localhost:27018`).

### Loading data without a GitHub token

Two different things live on disk, and they are **not** interchangeable:

- **`data/` — sanitized derived output.** What the pipeline writes, with the
  personal fields stripped (email, location, bio, company). Load and inspect
  this by default; it is safe to share.
- **`cache/` — the raw corpus.** Unmodified GitHub API response bodies that
  still contain personal data: email addresses and full `/users` profiles with
  location, bio and company. It is **private** and must never be republished,
  committed, uploaded or shared; it is stored mode `700`/`600` and git-ignored.

`make mongo-load` imports the sanitized `data/` tree into a `coops` database —
one collection per file, named `<layer>_<basename>`
(`data/bronze/members_basic.json` → `bronze_members_basic`). List files are
imported with `--jsonArray`, so their leading `_metadata` record is imported
too, mirroring the on-disk convention (see [domain.md](domain.md)). In a fresh
clone it first restores the newest snapshot when `data/bronze/` is absent, so
you get data in minutes with no `GITHUB_TOKEN`:

```bash
make mongo-load
```

Loading the raw corpus is an explicit, clearly-labelled opt-in, for offline
development against the raw-capture path only:

```bash
make mongo-load-raw     # imports the PRIVATE cache/ into the `raw` collection
```

**Snapshots.** Packing and restoring the corpus (both `cache/` and `data/`) is
done by `scripts/data-snapshot.sh`, which keeps archives private (`700`/`600`),
checksummed, and in a shared location outside the worktree. Because a snapshot
contains the raw corpus, it is private too and must not be published or shared:

```bash
make mongo-snapshot                     # scripts/data-snapshot.sh pack
./scripts/data-snapshot.sh unpack       # restore the newest snapshot
./scripts/data-snapshot.sh list         # what snapshots exist
```

**Producing the data yourself.** The pipeline writes `./data` and `./cache` in
the current directory; run it from a scratch directory and point the loader at
the result:

```bash
REPO=$PWD
cd "$(mktemp -d)"
uv run --project "$REPO" coops-bronze --max-repos 3 --skip-structure
uv run --project "$REPO" coops-silver
uv run --project "$REPO" coops-gold
"$REPO"/scripts/load_mongo_snapshot.sh "$PWD/data"
```

## Dashboard

```bash
cd dashboard
npm run dev              # http://localhost:5173
npm run test:coverage
npm run build:gh         # tsc -b && vite build, as the Pages deploy runs it
npm run lint             # not in CI; 319 pre-existing problems
```

To develop against a local extraction, set `VITE_USE_LOCAL_DATA=true` in
`dashboard/.env` and link the data: `mkdir -p dashboard/public && ln -s ../../data dashboard/public/data`.

**Changing dependencies:** regenerate the lockfile with npm 11
(`npx -y npm@11 install --package-lock-only`) — that is what produced the
committed lockfile. npm 10.8.2 regenerates it byte-identically today (an
earlier `Cannot read properties of null (reading 'edgesOut')` crash no longer
reproduces), and `npm ci` works on both, but npm 11 is the supported path.

## Running workflows locally with act

Full reference: [local-actions.md](local-actions.md). Defaults (runner images,
artifact server) come from the repository's `.actrc`.

```bash
gh act workflow_dispatch -W .github/workflows/bronze-extract.yaml \
  -j extract-bronze-data --secret-file .secrets --var COOPS_ORG=<org> \
  --bind --container-options "--user $(id -u):$(id -g)" \
  --input max_repos=3 --input skip_structure=true
```

- `--secret-file` fills `secrets.*` only; the workflow reads the organization
  from the `COOPS_ORG` **variable**, so pass `--var COOPS_ORG=…` or it extracts
  the owner of your `origin` remote.
- Silver and Gold need `--bind`: each job otherwise gets a fresh copy of the
  checkout, so the previous layer's data never reaches them.
- The container runs as root, and `uv sync` inside it rewrites `.venv` for the
  container's interpreter; `sudo rm -rf .venv` afterwards if `uv run` complains.
