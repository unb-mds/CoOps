# Running a phase

How a phase gets implemented, tested against real data, and merged. Read
[development-data.md](development-data.md) first — it covers where the data
comes from and what must not be written to.

## The shape

```
issue PR  ->  phase/<epic>-<slug>  ->  main
```

Issue PRs target the **phase branch**, never `main`. The phase merges to `main`
once consolidated and verified. Exception: a tooling or docs PR touching nothing
under `src/`, `data/` or `dashboard/` may go direct — but check first whether the
file differs between the two branches, or the merge silently reverts the phase's
newer version.

### `Closes #N` does not work here

A PR merged into a phase branch has its `Closes` keyword **ignored and spent**,
so the issue never closes — not then, and not on the eventual merge to `main`.
Write `Refs #N` and close by hand with evidence. This is how the tracker reached
84 open issues whose work had already shipped.

## Implementing

Heavy lifting goes to opencode agents; see the `opencode-agents` skill. Two
things the measurements here keep confirming:

- **Split anything large.** Dispatch cost grows with the *square* of the step
  count — two 50-step runs cost about half of one 100-step run. A brief that
  replaces 18 call sites across 7 modules is three dispatches, not one.
- **A prohibition in a brief is a preference, not a guardrail.** Agents have
  ignored explicit, capitalised instructions — opening draft PRs, editing fenced
  files. If something must not happen, verify it afterwards rather than trusting
  the prompt.

Every PR is gated by the agent who did **not** dispatch it. Approval is for a
**commit**: if the head moves, the approval is void and the gate runs again.

## Verifying against real data

A green suite says the code does what its tests expect. It does not say the
pipeline still produces correct data. That needs a regeneration.

### 1. Derive from the frozen snapshot

```bash
cd ~/.local/share/coops/snapshots
sha256sum -c coops-raw-<stamp>.tar.gz.sha256      # never skip
mkdir -p /var/tmp/coops-work
tar -xzf coops-raw-<stamp>.tar.gz -C /var/tmp/coops-work
cp -r  /var/tmp/coops-clean/data          /var/tmp/coops-work/data
cp     /var/tmp/coops-fga/watermarks.json /var/tmp/coops-work/    # NOT under data/
```

Seeding matters: a from-scratch tree has no previous state, so "did the defect
go away" has nothing to compare against.

Seed from a corpus you have **not** damaged. `/var/tmp/coops-fga/data` is the
obvious-looking choice and is the wrong one: an aborted run rewrote 69% of its
bronze files. Seed and `--reference` must be the same corpus, or the comparison
measures the difference between two baselines. `coops-clean/data` and
`coops-fga/watermarks.json` *are* a matched pair despite the names — verified by
mtime, both from the run ending 2026-09-23 16:42 local.

### The seeding choice is a trade, and neither side is safe yet

**`watermarks.json` sits at the tree root, not under `data/`**, so a step that
copies `data/` alone silently drops it. But restoring it is not simply a fix —
it changes which failure you get:

| seed | what the extractor asks for | result |
|---|---|---|
| **no watermarks** | unconditional listing URLs | **offline-ish, and wrong** — those bodies are the oldest in the cache |
| **with watermarks** | `since=<watermark>` URLs | **correct, and networked** — measured: misses → network on commits, and the issues body carries an ETag so it revalidates |

Without watermarks you get #199: 65% of cached bodies carry no `ETag`, and
`get_with_cache` serves an ETag-less body directly, forever, with no
revalidation. The run reads month-old listings, writes records *older* than the
ones it replaced, then persists a watermark describing what it just read —
moving the watermark **backwards** (measured: `2026-09-23T18:48:10Z` →
`2026-09-22T00:47:11Z`). It is invisible online, because the next networked run
repairs itself.

With watermarks you get correct data by going to the network, which the frozen
raw tier forbids during the refactor.

**So there is currently no configuration that is both frozen and correct**, and
the remaining 34% makes it worse: ETag'd bodies revalidate on every `--cache`
read, so *no* `--cache` run is offline regardless of watermarks. This is why
#195's offline mode is a blocker rather than a convenience. Until it lands, say
in the PR which side of the trade you took and prove it by measurement, never by
intent.

### Verify the watermark did not regress — over all 486, not a sample

```bash
python3 - /var/tmp/coops-fga/watermarks.json /var/tmp/coops-work/watermarks.json <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))["repos"]; b=json.load(open(sys.argv[2]))["repos"]
def ts(d,k): return (d[k].get("last_updated_at") or "")   # present but null for 46 of 486
back=[k for k in a if k in b and ts(b,k) < ts(a,k)]
print(f"{len(back)} of {len(b)} repositories moved backwards")
for k in back[:10]: print("  ", k, ts(a,k), "->", ts(b,k))
PY
```

Check every repository. A hand-picked one samples the population it claims to
describe, which is how several wrong conclusions here were reached. Run against
the pair that produced #199 it reports:

```
5 of 486 repositories moved backwards
   fga-eps-mds/MeasureSoftGram-Core              2026-09-22T11:51:14Z -> 2026-09-11T14:55:02Z
   fga-eps-mds/2026.2-MeasureSoftGram-DOC        2026-09-23T19:13:59Z -> 2026-09-21T17:55:45Z
   fga-eps-mds/2026.2-UNB-FCTE_UNIEURO_MED-DOCS  2026-09-23T17:32:35Z -> 2026-09-22T03:44:30Z
   fga-eps-mds/2026.2-UNB-FCTE_UNIEURO_MED-APP   2026-09-23T18:48:10Z -> 2026-09-22T00:47:11Z
   fga-eps-mds/2026-2-AnatoQuizUp-Doc            2026-09-23T18:46:05Z -> 2026-09-17T19:42:04Z
```

Note `last_updated_at` is **present but null** for 46 of the 486, so a naive
`.get(key, "")` returns `None` and the comparison raises rather than reporting
zero — which is the better failure of the two, but only because it is loud.

(`watermarks.json` has two top-level keys, `version` and `repos`. Counting the
top level reports **2**; the repositories are the 486 under `repos`. That
miscount has been made here twice.)

### 2. Run the layers

```bash
COOPS_WORK=/var/tmp/coops-work \
REQUIRE_MARKERS="<symbols this run depends on>" \
REQUIRE_ANCESTOR="<commit whose work this run needs>" \
REQUIRE_BASELINE="<the defect being removed>" \
  ./regen-all-layers.sh
```

The guards are the point. `REQUIRE_ANCESTOR` refuses a run against a head that
predates the fix — otherwise the run reproduces the defect in fresh data and
reports success. `REQUIRE_BASELINE` refuses unless the defect is *present to
begin with*, because "clean afterwards" proves nothing if it was clean before.

`coops-registry` runs **last and is not optional**: the manifests are generated
by scanning the layers, so skipping it leaves them advertising files that no
longer exist.

### 3. Check the medallions

```bash
uv run python scripts/verify_medallion.py --self-test          # controls first
uv run python scripts/verify_medallion.py \
    --root /var/tmp/coops-work \
    --reference /var/tmp/coops-clean        # REQUIRED for a gate
```

Point `--reference` at a real, *uncontaminated* previous corpus. Today that is
`/var/tmp/coops-clean`, restored from the 22:45 tarball. Do not invent a path:
the reference is the only thing that makes "vanished" and "reverted" meaningful,
and a reference that is itself the output of a bad run turns both checks into
confirmations of that run.

**Run `--self-test` first, every time.** It plants a defect for each check and
confirms the check rejects it. A check that has never failed proves nothing, and
this script exists because the regeneration script spent three iterations
enforcing a baseline and never checking the outcome.

`--reference` is not optional for a phase gate. Without it the two staleness
checks cannot run, and records that vanished or reverted since the last run are
invisible — so the script exits **2**, not 0. Point it at the previous run's
corpus, or a restored snapshot of it.

Exit codes: `0` pass, `1` a layer fails an invariant, `2` a check could not run
or a control did not fire. **`2` is not a weaker `1`** — it means the instrument
is broken, not the data.

### 4. Read the numbers, not the verdict

The regeneration prints per-layer addresses and record counts. Both matter, and
for opposite reasons:

- **Records must not fall** in Bronze — a drop means data was dropped.
- **Records falling in Silver is ambiguous.** Once identity merging lands
  (#171), a correct run produces *fewer* Silver records, and the criterion must
  measure the fall against the number of merged identities rather than fail it
  outright.
- "Fewer addresses" alone is exactly as consistent with having destroyed the
  data as with having cleaned it. That is why both are reported.

### Known non-determinisms — not regressions

- `_metadata.extracted_at`, `updated_at`, `generated_at` differ between any two
  runs.
- Record **order** within a file changed at #170 (per-repository files are read
  filename-sorted). Values must not.
- The first run after #188 shifts `account_age_days`, `maturity_score` and
  `status` once, from "age today" to "age at capture".

## Merging the phase

1. Regeneration passes, or its failures are understood and attributed.
2. `verify_medallion.py` green, with `--self-test` green.
3. Full suite and `mypy` green **on the merged result**, not on the branch — two
   branches can each be green and their merge red.
4. Close by hand every issue whose `Closes` was spent on the phase branch. The
   running list lives as a comment on the phase epic. **Do not generate it from
   the commit log**: commits cite PR numbers, not issue numbers, so a
   `git log --grep` sweep misses work that shipped.

## Predictions

State what a run should produce **before** it runs, in a place that survives —
the issue, or the bus. It is the only way to tell a result that confirms
something from a result you explained afterwards.

And say which *quantity* you are predicting. A prediction about identity merging
is not a prediction about record counts, and stating one as the other produces a
wrong prediction from correct reasoning.
