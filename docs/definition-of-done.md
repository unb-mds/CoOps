# Definition of Done

What "done" means for a change in this repository. It is referenced by
`.opencode/agents/reviewer.md`, `.opencode/agents/tester.md`, `AGENTS.md` and the
pull request template, so that one standard is enforced everywhere rather than
re-argued per PR.

Reviewers enforce this. A change that misses it is *merge after fixes*, not
"approve with a note".

This file is normative and self-contained — read it and you can do the work. The
failure behind each rule in *Verification* is in
[dod-cases.md](dod-cases.md); open it when a rule looks like bureaucracy or a
gate disputes whether it applies, not before.

## Every change

- **Unit tests for the behaviour you changed.** Not for the paths that already
  worked. If you cannot name the regression a test would catch, you have not
  found the behaviour yet.
- **The suite passes, and you quote the counts** — `N passed, M skipped` — not
  the word "passes". See *Verification* below.
- **Lint, format and type-check pass.** `ruff` for Python; `npm run lint`,
  `npm run format:check` and `tsc -b` for the dashboard. Run them locally; do
  not discover it in CI.
- **`CHANGELOG.md` updated** under `[Unreleased]` for anything user-visible,
  including a changed data shape — a new or removed field changes what the
  dashboard sees on the next run.
- **No new personal data reaches a published artifact.** Grep the *output*, not
  the code: a sweep for field names never finds an address sitting inside free
  text.
- **A projection that crosses a publish boundary selects the fields it uses.**
  Never spread a provider response and subtract (`{**issue}` minus a denylist):
  a whitelist is bounded by what we use, a denylist by what we have *noticed*.
  This applies to what gets published — **not** to the raw tier, which
  deliberately keeps what the provider sent.
- **A new field or output has a named consumer**, or an issue saying when it
  gets one. A value nothing reads is untested by construction: its tests assert
  that the writer ran, not that anything depends on the result.

## When the change crosses a boundary

- **Integration tests** wherever the change spans extraction ↔ store, API ↔
  database, or layer ↔ layer, and one can be written without a live third party.
  Fake at the boundary — replace the port, let the code under test run for real.
  Patching internal functions freezes the implementation and blocks refactoring.
- **Run it against something real** when it touches extraction, storage or
  serving: the smallest slice that exercises the path, from a scratch directory,
  and report the numbers you saw with a command anyone can repeat.
- **A workflow change is demonstrated, not reasoned about.** Run it (`gh act`,
  see [local-actions.md](local-actions.md)) against a tree where the step
  actually does its work. A shell step that stages nothing still exits 0, and
  `|| echo` turns a failure into a green job.

## Added as the capability lands

- **Contract tests** between the dashboard and the serving API once that API
  exists (#44). The shape is asserted from one source so the two cannot drift.
- **End-to-end tests** in a real browser once the dashboard runs on its own
  server (#62).
- **Performance budgets** once routes are split (#122) — an initial bundle size
  that fails the build when exceeded, so a regression is caught on the PR that
  causes it rather than months later.

## Verification

Every rule here was written after a green result turned out to mean nothing.
That is the dominant defect class in this repository, so these are not style
preferences — each one is the difference between a check that measured the
change and a check that measured its own setup. The case behind each is in
[dod-cases.md](dod-cases.md).

- **A test must be able to fail.** Mutate the thing you changed *in the shipped
  source*, run the suite, and name the test that went red. A green run after a
  mutation means the scope was wrong or nothing covers that line — both are
  findings, not a pass.
- **Prove the mutation landed before you trust its result.** `cmp -s` against a
  saved copy. A mutation that silently failed to apply produces a green run that
  looks exactly like a well-tested guard.
- **Mutate the guard, not the module.** Deleting a name so the file raises
  `NameError` proves only that the module parses. Change the *decision* the
  guard makes.
- **Read the count, not the verdict.** Quote `N passed, M skipped` against the N
  you expected, and re-derive the base from a clean checkout *before* you write.
  A baseline measured afterwards contains your own work.
- **A guard is proven by what did not happen.** Assert the effect, not the error:
  that the record is absent, not that a message was logged.
- **Run a control before trusting an empty result.** "Found none" and "cannot
  find any" are indistinguishable from the outside. Make the instrument find one
  on purpose first.
- **"Absent from the corpus I measured" is not "cannot occur."** Before deleting
  a guard as dead, ask what the code could be pointed at, not only what it was
  pointed at today. (#156: a guard that excluded zero files still prevented
  double-counting 142 real ones.)
- **Precedence needs both values present at once.** Generating each input kind in
  isolation can never find a precedence bug. One fixture, both values, assert
  which wins.
- **A data migration needs two assertions.** *Nothing was lost* and *nothing was
  wrongly changed*. A row count satisfies the first while the second fails
  silently — that is how 230 people kept their rows and lost their names.
- **A two-arm comparison needs proof the arms differ.** Hash the module, not the
  function. A control proves the instrument can see; an arms-differ assert proves
  it saw two different things.
- **Assert on the whole artifact; truncate only the display.** Checking the first
  N characters of a value finds what happens to sit in the first N characters.
- **Measure the subject you are claiming about.** Nearly every disagreement here
  resolved to two people measuring *different things*, not to a wrong arithmetic:
  aggregates vs per-repo files, `data/` vs `data/bronze/`, mapping vs
  attribution. Say what you counted.
- **Verify the terminal state, not the step you just took.** "The push
  succeeded" is not "it merged"; "the process started" is not "the agent ran".

## Tests that do not count

- Assertions on log text or printed output.
- `try/except` that turns a failure into a skip. If a dependency is genuinely
  unavailable, skip on that condition explicitly, naming it.
- Anything that reads or writes the working directory. A test whose result
  depends on whether `data/` happens to exist in the checkout is a bug in the
  test.
- Fixtures that assert what the code just did.
- A floor chosen so low it cannot be breached. Assertions grow, so a lagging
  floor is fine; files do not, so pin their real number.
