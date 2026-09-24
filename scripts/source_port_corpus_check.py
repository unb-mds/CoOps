"""Corpus check for the SourcePort: replay a captured corpus through it.

Reads a ``data/`` tree (the Medallion output of a real extraction) with a
read-only fake adapter that satisfies ``coops.domain.ports.SourcePort``,
feeds every record through the GitHub mapper, and reports records
processed and failures per entity kind. This is the "run it against
something real" leg of ``docs/definition-of-done.md`` for changes to the
source port; the unit/contract suite is hermetic by design and proves the
contract, not the data.

The adapter presents each Bronze record to the mapper in the *provider*
shape the mapper documents (``coops.github.mapper``), reversing the three
renames the Bronze projection made: the commit account link back to the
top-level ``author``, string parents back to ``{"sha": ...}`` objects, and
the tree entry vocabulary back to Git's (``file``/``directory`` were
``blob``/``tree`` before extraction standardised them). Two corpus facts
cannot round-trip and are reported rather than hidden: commit emails were
already reduced to a hash by the Bronze scrub, so hash-keyed authors map
by name (or to ``author=None``), and PR ``merged_at``/``draft`` were
dropped by the projection, so replayed PRs carry ``merged_at=None``. Both
paths are covered synthetically in ``tests/unit/test_github_mapper.py``
and the port contract tests.

Replay semantics (a corpus is not a live provider):

- The corpus holds **one** tenant. A call for any other tenant reads
  nothing — empty iterators, not-found repositories — which makes the
  port's tenant scoping observable even against a single-tenant corpus.
- A repository absent from ``repositories_detailed.json`` raises
  ``SourceNotFoundError``. A repository present whose per-repository file
  was never captured replays as an empty iterator: the repository exists,
  the capture simply holds no records of that kind.
- A record that fails to map is counted and skipped rather than raised —
  the failure tally is the measurement this harness exists to produce.

Usage (read-only; the directory is never written):

    uv run python scripts/source_port_corpus_check.py \
        --data-dir /path/to/data --tenant <org>

Addresses only per-repository files and the two organisation-wide
datasets, so the ``*_all.json`` publish aggregates are excluded by
construction. Exits non-zero if any record fails to map, a population
comes back empty, or a second tenant reads anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from coops.domain import (
    Commit,
    FileTree,
    Issue,
    Member,
    PullRequest,
    Repository,
    TenantId,
)
from coops.domain.ports import SourceNotFoundError, SourcePort
from coops.github.mapper import (
    map_commit_rest,
    map_file_tree_rest,
    map_issue,
    map_member,
    map_pull_request,
    map_repository,
)

#: Extraction-standardised tree kinds -> Git's own vocabulary, which is
#: what the mapper (and both provider shapes) speak.
_KIND_TO_GIT = {"file": "blob", "directory": "tree", "commit": "commit"}

#: What a mapper raises on a record it cannot build: the models' own
#: guards (ValueError), a payload of the wrong shape reaching a helper
#: (TypeError/AttributeError/KeyError). Anything else is a bug in this
#: script and crashes loudly rather than being tallied.
MAPPER_FAILURES = (TypeError, ValueError, KeyError, AttributeError)


@dataclass
class KindStats:
    """Per-entity-kind tally for one run."""

    records: int = 0
    failures: int = 0
    #: (file stem, error) of the first failure, for the report.
    first_failure: tuple[str, str] | None = None

    def count(self) -> None:
        self.records += 1

    def fail(self, stem: str, error: Exception) -> None:
        self.failures += 1
        if self.first_failure is None:
            self.first_failure = (stem, f"{type(error).__name__}: {error}")


@dataclass
class CorpusSource:
    """A `SourcePort` replaying a captured ``data/bronze`` tree, read-only.

    Satisfies the port structurally (``isinstance(source, SourcePort)``)
    and honours its contract: every method scopes to the ``TenantId`` it
    is given, and a repository the corpus does not hold raises
    ``SourceNotFoundError`` for that tenant rather than answering with
    another tenant's — or no — data.
    """

    data_dir: Path
    tenant: TenantId
    stats: dict[str, KindStats] = field(default_factory=dict)
    repos: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        bronze = self.data_dir / "bronze"
        if not bronze.is_dir():
            raise SystemExit(f"no bronze/ directory under {self.data_dir}")
        for raw in self._load("repositories_detailed"):
            if isinstance(raw, dict) and raw.get("name"):
                self.repos[raw["name"]] = raw

    # -- helpers ---------------------------------------------------------

    def _kind(self, kind: str) -> KindStats:
        return self.stats.setdefault(kind, KindStats())

    def _load(self, stem: str) -> list:
        path = self.data_dir / "bronze" / f"{stem}.json"
        if not path.is_file():
            return []
        with open(path, encoding="utf-8") as handle:  # read-only, always
            data = json.load(handle)
        if not isinstance(data, list):
            return [data]
        # First element may be the extractor's _metadata header record.
        if data and isinstance(data[0], dict) and "_metadata" in data[0]:
            return data[1:]
        return data

    def _scoped(self, tenant: TenantId) -> bool:
        """True when the call addresses the one tenant the corpus holds."""
        return str(tenant) == str(self.tenant)

    def _known_repo_records(self, kind: str, tenant: TenantId, repo_name: str) -> list:
        if not self._scoped(tenant) or repo_name not in self.repos:
            # A repository this corpus does not hold does not exist *for
            # this tenant*: loud, never silently someone else's data.
            raise SourceNotFoundError(f"no repository {repo_name!r} for this tenant")
        return self._load(f"{kind}_{repo_name}")

    # -- the port --------------------------------------------------------

    def fetch_repositories(self, tenant: TenantId) -> Iterator[Repository]:
        kind = self._kind("repositories")
        if not self._scoped(tenant):
            return
        for raw in self._load("repositories_detailed"):
            if not isinstance(raw, dict):
                continue
            try:
                yield map_repository(raw, tenant)
                kind.count()
            except MAPPER_FAILURES as error:  # measured, then reported
                kind.fail("repositories_detailed", error)

    def fetch_members(self, tenant: TenantId) -> Iterator[Member]:
        kind = self._kind("members")
        if not self._scoped(tenant):
            return
        for raw in self._load("members_detailed"):
            if not isinstance(raw, dict):
                continue
            try:
                # The profile shape, with the projection's rename undone.
                payload = dict(raw)
                payload["contributions"] = raw.get("contributions_total") or 0
                yield map_member(
                    payload, tenant, is_org_member=bool(raw.get("is_org_member"))
                )
                kind.count()
            except MAPPER_FAILURES as error:
                kind.fail("members_detailed", error)

    def fetch_commits(self, tenant: TenantId, repo_name: str) -> Iterator[Commit]:
        kind = self._kind("commits")
        for raw in self._known_repo_records("commits", tenant, repo_name):
            if not isinstance(raw, dict):
                continue
            try:
                # Bronze moved the account link into commit.author and
                # flattened parents to strings; the mapper reads the
                # provider's placement, so put them back. The email is
                # gone by design (scrubbed at capture) and stays absent.
                git_author = dict((raw.get("commit") or {}).get("author") or {})
                login, account_id = git_author.get("login"), git_author.get("id")
                yield map_commit_rest(
                    {
                        "sha": raw.get("sha"),
                        "author": (
                            {"login": login, "id": account_id}
                            if login or account_id is not None
                            else None
                        ),
                        "commit": {
                            "author": {
                                "name": git_author.get("name"),
                                "email": None,
                                "date": git_author.get("date"),
                            },
                            "committer": (raw.get("commit") or {}).get("committer"),
                            "message": (raw.get("commit") or {}).get("message"),
                        },
                        "parents": [{"sha": sha} for sha in raw.get("parents") or []],
                        "stats": {
                            "additions": raw.get("additions"),
                            "deletions": raw.get("deletions"),
                        },
                    },
                    tenant,
                    repo_name,
                )
                kind.count()
            except MAPPER_FAILURES as error:
                kind.fail(repo_name, error)

    def fetch_issues(self, tenant: TenantId, repo_name: str) -> Iterator[Issue]:
        kind = self._kind("issues")
        for raw in self._known_repo_records("issues", tenant, repo_name):
            if not isinstance(raw, dict):
                continue
            try:
                yield map_issue(raw, tenant, repo_name)
                kind.count()
            except MAPPER_FAILURES as error:
                kind.fail(repo_name, error)

    def fetch_pull_requests(
        self, tenant: TenantId, repo_name: str
    ) -> Iterator[PullRequest]:
        kind = self._kind("prs")
        for raw in self._known_repo_records("prs", tenant, repo_name):
            if not isinstance(raw, dict):
                continue
            try:
                # The projection split PRs off the shared endpoint and
                # dropped the marker object; restore it (merged_at is not
                # in the corpus, so it replays as None — see the module
                # docstring).
                yield map_pull_request(
                    {**raw, "pull_request": {"merged_at": raw.get("merged_at")}},
                    tenant,
                    repo_name,
                )
                kind.count()
            except MAPPER_FAILURES as error:
                kind.fail(repo_name, error)

    def fetch_tree(
        self, tenant: TenantId, repo_name: str, branch: str | None = None
    ) -> FileTree:
        kind = self._kind("structure")
        if not self._scoped(tenant) or repo_name not in self.repos:
            raise SourceNotFoundError(f"no repository {repo_name!r} for this tenant")
        captured = self._load(f"structure_{repo_name}")
        if not captured:
            # No tree was captured; an empty FileTree would claim
            # completeness by existing (the model's own rule), so this is
            # a not-found, not a fabricated empty snapshot.
            raise SourceNotFoundError(f"no captured tree for {repo_name!r}")
        payload = captured[0]
        wanted = branch or self.repos[repo_name].get("default_branch")
        if payload.get("branch") != wanted:
            raise SourceNotFoundError(
                f"branch {wanted!r} was not captured for {repo_name!r}"
            )
        entries = [
            {**entry, "type": _KIND_TO_GIT.get(entry.get("type"), entry.get("type"))}
            for entry in payload.get("tree") or []
            if isinstance(entry, dict)
        ]
        try:
            tree = map_file_tree_rest(
                {
                    "sha": payload.get("sha"),
                    "truncated": payload.get("truncated"),
                    "tree": entries,
                },
                tenant,
                repo_name,
                branch=branch,
            )
        except MAPPER_FAILURES as error:
            kind.fail(repo_name, error)
            raise
        kind.count()
        return tree


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--tenant", required=True, help="the org the corpus holds")
    args = parser.parse_args()

    tenant = TenantId(args.tenant)
    source = CorpusSource(data_dir=args.data_dir, tenant=tenant)
    if not isinstance(source, SourcePort):  # the fake must satisfy the port
        print("CorpusSource does not satisfy SourcePort")
        return 1

    repositories = list(source.fetch_repositories(tenant))
    members = list(source.fetch_members(tenant))
    commits = issues = prs = trees = not_captured = tree_failures = 0
    for repo in repositories:
        commits += sum(1 for _ in source.fetch_commits(tenant, repo.name))
        issues += sum(1 for _ in source.fetch_issues(tenant, repo.name))
        prs += sum(1 for _ in source.fetch_pull_requests(tenant, repo.name))
        try:
            source.fetch_tree(tenant, repo.name)
            trees += 1
        except SourceNotFoundError:
            not_captured += 1
        except MAPPER_FAILURES:  # already tallied inside fetch_tree
            tree_failures += 1

    # Every model the port handed back must carry the tenant it was read
    # under — one corpus tenant, asserted end to end.
    wrong_tenant = sum(1 for r in repositories if r.tenant != tenant) + sum(
        1 for m in members if m.tenant != tenant
    )

    # A second tenant reads nothing: not the corpus tenant's records.
    other = list(source.fetch_repositories(TenantId("corp-someone-else")))

    print(f"tenant                      {tenant}")
    print(f"repositories                {len(repositories)}")
    print(f"members                     {len(members)}")
    print(f"commits                     {commits}")
    print(f"issues                      {issues}")
    print(f"pull requests               {prs}")
    print(f"trees                       {trees} (not captured: {not_captured})")
    print(f"records on wrong tenant     {wrong_tenant} (must be 0)")
    print(f"second tenant repositories  {len(other)} (must be 0)")
    ok = True
    for kind in sorted(source.stats):
        stats = source.stats[kind]
        line = f"{kind:26s}  processed={stats.records:7d}  failures={stats.failures}"
        if stats.first_failure:
            stem, error = stats.first_failure
            line += f"  first: {stem}: {error}"
        print(line)
        if stats.failures:
            ok = False
    if tree_failures:
        print(f"tree mapping failures       {tree_failures}")
        ok = False
    if not (repositories and members and commits and issues and prs and trees):
        print("EXPECTED NON-EMPTY POPULATIONS — got an empty kind")
        ok = False
    if wrong_tenant or other:
        print("TENANT SCOPE LEAK")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
