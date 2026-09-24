# Testing a pull request before review

CoOps extracts data from real GitHub organizations, and unit tests alone
don't prove that an extraction works. So every PR that touches the pipeline,
its workflows or its packaging is validated **end to end before** it is marked
*Ready for review*.

That validation runs **on your own machine**, with
[`act`](https://github.com/nektos/act): `act` executes the real workflow files
in a container that imitates a GitHub-hosted runner, so you get the same
result without needing push access to any fork. See
[local-actions.md](local-actions.md) for the full reference.

```
branch ──► draft PR
   │
   ├─ 1. local checks (tests + capped local run)
   ├─ 2. "Validate Pipeline (manual)" locally with `gh act`
   ├─ 3. paste the local run's summary in the PR, mark it Ready for review
   │
maintainer: reviews, re-runs validation if needed, approves, merges
```

## Roles

| Who | Does |
|---|---|
| Author | Pushes the branch, opens a **draft** PR, runs steps 1–3 |
| Maintainer | Reviews the code, checks the validation output, approves and merges into `danrleypereira/CoOps` |

## 0. One-time setup

```bash
uv sync
cp EXAMPLE.secrets .secrets        # set GITHUB_TOKEN and GITHUB_ORG

# local Actions runner
gh extension install nektos/gh-act
docker pull catthehacker/ubuntu:act-latest
```

Nothing here needs push access to a fork. If you have an old system-wide
`act` binary (0.2.81 or older, from the `install.sh` line in
[RUNNING_LOCALLY.md](../RUNNING_LOCALLY.md)), remove it: it is affected by
CVE-2026-34041 / CVE-2026-34042. Use `gh act`.

> Run `act` from a normal clone, not from a `git worktree` — a worktree's
> `.git` is a file pointing outside the container, and any workflow step that
> shells out to `git` dies with `fatal: not a git repository: (null)`.

## 1. Branch and draft PR

```bash
git switch -c feat/<issue>-<topic> origin/main
# ...commit...
git push -u origin feat/<issue>-<topic>

gh pr create --draft --repo danrleypereira/CoOps --base main
```

Opening the PR as a draft signals that it is not validated yet.

> GitHub Actions is disabled in `danrleypereira/CoOps` by design, so no checks
> appear on the PR. That is what steps 2 and 3 replace: the same workflow
> files, run locally.

## 2. Local checks

```bash
uv run pytest

# Capped run against the real organization (under a minute).
# The commands write to ./data and ./cache, so run them from a scratch
# directory instead of your checkout.
REPO=$PWD
cd "$(mktemp -d)"                    # fresh directory: no data from earlier runs
export GITHUB_TOKEN=$(gh auth token) GITHUB_ORG=unb-mds
uv run --project "$REPO" coops-bronze --max-repos 3 --max-issues 20 --max-prs 20 \
  --max-commits-per-repo 50 --skip-structure
uv run --project "$REPO" coops-silver
uv run --project "$REPO" coops-gold
uv run --project "$REPO" coops-aggregate
uv run --project "$REPO" coops-registry
jq '.organization_health' data/gold/executive_dashboard.json
cd "$REPO"
```

If the frontend changed, also run `cd dashboard && npm run test:coverage`.

## 3. Validate the workflows locally with `act`

The **Validate Pipeline (manual)** workflow
(`.github/workflows/validate-pipeline.yaml`) runs the test suite and then the
whole pipeline (Bronze → Silver → Gold → Aggregate → Registry, optionally the
AI analysis) against a real organization. It **does not commit or push**, and
under `act` the generated `data/` stays inside the container, so your checkout
is untouched.

Run it, and the two CI workflows, from the repository root:

```bash
# CI: unit tests (Python 3.10/3.11 + frontend Node 20/22) — no token needed
gh act workflow_dispatch -W .github/workflows/python-unit-tests.yaml

# CI: integration tests (Python 3.10/3.11/3.12) — no token needed
gh act workflow_dispatch -W .github/workflows/python-integration-tests.yaml

# End-to-end validation against a real organization
gh act workflow_dispatch -W .github/workflows/validate-pipeline.yaml \
  --secret COOPS_GITHUB_TOKEN="$(gh auth token)" \
  --var COOPS_ORG=unb-mds \
  --input max_repos=2 \
  --input max_issues=10 \
  --input max_prs=10 \
  --input max_commits_per_repo=10
```

`act` exits non-zero if any job fails, so `&&`-chaining them works. Runner
image, artifact server and the rest of the defaults come from the repository's
[`.actrc`](../.actrc).

Measured, with warm caches: unit tests 113 s for the full matrix, integration
tests 27 s, validation 86 s with those caps. The first run is several minutes
longer (image pull, action clones). Raise `max_repos` for a more thorough
extraction; drop the caps entirely for a full one (slow).

> Pass the token on the command line as shown — never write a real token into
> a file that could be committed. `--secret-file .secrets` also works;
> `.secrets` is gitignored.

The **Check outputs** step prints the same table as on GitHub:

```
### Pipeline validation: `unb-mds`

| File | Status | Records |
|---|---|---|
| `bronze/repositories_filtered.json` | ok | 2 |
| `bronze/issues_2024-example.json` | ok | 18 |
…
🏁  Job succeeded
```

What a green run means: the tests passed, every command exited 0, and the
**Check outputs** step found every expected Bronze/Silver/Gold/registry file
as valid JSON, with at least one repository extracted.

### Inputs

| Input | Default | Notes |
|---|---|---|
| `org` | `COOPS_ORG` variable, else the repo owner | Must be an organization |
| `max_repos` / `max_issues` / `max_prs` / `max_commits_per_repo` | 3 / 20 / 20 / 50 | Blank = no cap (full extraction, slow) |
| `since` | blank | ISO-8601, limits commit history |
| `skip_structure` | `true` | Structure extraction is slow |
| `run_ai` | `false` | Needs `--secret GEMINI_API_KEY=…` |

`docs/local-actions.md` has the per-workflow reference, the troubleshooting
list, and what cannot be run locally (`deploy-pages.yaml`, which needs the
real GitHub Pages service).

> If you change `dashboard/package.json`, regenerate the lockfile with npm 11
> (`npx npm@11 install --package-lock-only`) — that is what produced the
> committed lockfile. npm 10.8.2 regenerates it byte-identically today, and
> `npm ci` works on both, but npm 11 is the supported path.

> Which workflow to use: `validate-pipeline.yaml` is for PRs.
> `bronze-extract.yaml` and the Silver/Gold workflows it chains are the
> production pipeline: they **commit data to the branch they run on**. Don't
> dispatch them on a PR branch. Locally they need `--bind` to pass data
> between layers, which writes into your working tree as `root` — see
> [local-actions.md](local-actions.md#the-layer-workflows-bronze-extractyaml-silver-processyaml-gold-processyaml-gold-aggregateyaml).

## 4. Mark the PR ready

Add the run links (validation, unit tests, integration tests) to the
**Organization validation** section of the PR description, then:

```bash
gh pr ready <number> --repo danrleypereira/CoOps
```

Paste the `Check outputs` summary table and the `🏁 Job succeeded` lines from
step 3, and say which commit you validated. Push new commits? Re-run step 3
and update the description: the validation must match the PR's latest commit.

## 5. Maintainer: approve and merge

The PR description must carry a validation for the PR's **latest** commit. If
it doesn't, run step 3 yourself on the PR branch:

```bash
PR=<number>
gh pr checkout "$PR" --repo danrleypereira/CoOps

gh act workflow_dispatch -W .github/workflows/python-unit-tests.yaml \
  && gh act workflow_dispatch -W .github/workflows/python-integration-tests.yaml \
  && gh act workflow_dispatch -W .github/workflows/validate-pipeline.yaml \
       --secret COOPS_GITHUB_TOKEN="$(gh auth token)" --var COOPS_ORG=unb-mds \
       --input max_repos=2 --input max_commits_per_repo=10

gh pr review "$PR" --repo danrleypereira/CoOps --approve
gh pr merge "$PR" --repo danrleypereira/CoOps --squash --delete-branch
```

## Appendix: validating on a real fork (optional)

Local `act` runs are the day-to-day path and cover everything except
`deploy-pages.yaml`. A real fork is still the only way to exercise GitHub
Pages, `workflow_run` triggers and the production commit-and-push steps, so
the workflows are re-checked on a fork occasionally — not per PR.

> The `unb-mds/CoOps` fork is being retired; a clean fork in a new
> organization will replace it. Read `<org>` below as whichever fork is
> current.

```bash
BRANCH=$(git branch --show-current)
SHA=$(git rev-parse HEAD)              # must already be pushed
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)

gh workflow run validate-pipeline.yaml --repo <org>/CoOps --ref "$BRANCH" \
  -f org=<org> -f max_repos=3 -f max_issues=20 -f max_prs=20

# follow the run just dispatched for this exact commit (it takes a few
# seconds to appear; older runs on the same commit are ignored)
sleep 10
RUN_ID=$(gh run list --repo <org>/CoOps --workflow validate-pipeline.yaml \
  --commit "$SHA" --event workflow_dispatch --json databaseId,createdAt \
  --jq "[.[] | select(.createdAt >= \"$START\")][0].databaseId")
gh run watch "$RUN_ID" --repo <org>/CoOps --exit-status

# inspect the generated data locally
gh run download "$RUN_ID" --repo <org>/CoOps --dir /tmp/coops-artifact

# the regular CI workflows (Python version matrix, frontend tests)
for w in python-unit-tests.yaml python-integration-tests.yaml; do
  gh workflow run "$w" --repo <org>/CoOps --ref "$BRANCH"
done
```

The generated `data/` is uploaded as the artifact `pipeline-data-<run id>`
(kept 7 days). A public fork means anyone signed in to GitHub can download
it: fine for public organization data. When `COOPS_GITHUB_TOKEN` is set (it
can read private data), the upload is skipped.

Optional repository settings in the fork: variable `COOPS_ORG`, secret
`COOPS_GITHUB_TOKEN` (see
[RUNNING_LOCALLY.md](../RUNNING_LOCALLY.md#organization-token-for-github-actions))
and secret `GEMINI_API_KEY`.

> GitHub only dispatches workflows whose file exists on the repository's
> default branch; the run itself uses the file from `--ref`. A PR that adds a
> new dispatchable workflow must have it registered on the fork's `main`
> first. `act` has no such restriction, which is another reason to validate
> locally.

### Sync the fork

The fork's `main` receives the pipeline's data commits, so it is never
identical to upstream: bring upstream in with a **merge**, from a clone of
the fork.

```bash
git fetch origin && git fetch upstream
git switch main && git merge --ff-only origin/main
git merge --no-edit upstream/main
# On conflicts:
#   data/...  -> keep the fork's data:   git checkout --ours -- data/ && git add data/
#   any other file (e.g. a workflow registered on the fork) -> take upstream:
#                                        git checkout --theirs -- <file> && git add <file>
#   then: git commit --no-edit
git push origin main
```

The push to `main` starts the pipeline (Bronze → Silver → Gold → KPIs →
Pages deploy) with the merged code; follow it with
`gh run list --repo <org>/CoOps --branch main --limit 3`.

> **Never** use `gh repo sync --force` or `git push --force` on the fork's
> `main`: it resets `main` to upstream and deletes the committed data.
