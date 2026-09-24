<!-- Thanks for contributing to CoOps! Please fill in the sections below. -->

## Summary

<!-- One or two sentences describing what this PR changes and why. -->

## Related issue

Closes #

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would change existing behavior)
- [ ] Documentation update
- [ ] CI / tooling

## Affected layers

- [ ] Bronze (extraction)
- [ ] Silver (analytics)
- [ ] Gold (KPIs)
- [ ] Frontend (dashboard)
- [ ] AI analysis
- [ ] Documentation / templates

## Test plan

<!-- How did you verify this? Commands you ran, files you inspected, manual
checks you performed. -->

See [docs/definition-of-done.md](docs/definition-of-done.md) — a reviewer will check against it.

- [ ] `pytest` passes locally — paste the counts (`N passed, M skipped`), not "passes"
- [ ] `cd dashboard && npm run test:coverage` passes locally — counts too
- [ ] `ruff` / `npm run lint` / `npm run format:check` / `tsc -b` all clean
- [ ] Tests added for the behaviour this PR changes
- [ ] Each new test was shown to fail without the fix — say which named test
- [ ] Integration test added, or a reason it isn't possible here
- [ ] `CHANGELOG.md` updated under `[Unreleased]` if user-visible (incl. data-shape changes)
- [ ] No new personal data reaches published output (grep the output, not the code)

## Organization validation

<!-- Required when the PR touches src/, workflows, or packaging. Open the PR as a
draft, run "Validate Pipeline (manual)", "Unit Tests" and "Python Integration
Tests" in unb-mds/CoOps on this branch, and paste the run links below. Then
mark the PR "Ready for review". See docs/TESTING_PULL_REQUESTS.md. -->

- Validate Pipeline: <!-- https://github.com/unb-mds/CoOps/actions/runs/... -->
- Unit Tests: <!-- link -->
- Python Integration Tests: <!-- link -->
- [ ] All runs are green and were made on the PR's latest commit
- [ ] Not applicable (docs/frontend-only change)

## Checklist

- [ ] My commits follow Conventional Commits (`feat:`, `fix:`, `docs:`, ...)
- [ ] I have read `CONTRIBUTING.md`
- [ ] My code is licensed under GPL-3.0-or-later (default for this repo)
- [ ] AI-assisted changes (if any) have been reviewed and validated by a human
