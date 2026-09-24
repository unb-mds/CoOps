"""Silver determinism tests (issue #172).

Sets were being serialized with a bare ``list()``, so the emitted order
depended on ``PYTHONHASHSEED`` and every Silver run produced a different
file. The fix sorts every set before it reaches an artifact.

Two properties are asserted here, and the second one is the proof:

1. In-process: the emitted lists equal an explicit, hand-written expected
   list (not merely ``== sorted(x)``, which any list satisfies trivially
   once written by hand — the explicit list pins the alphabet).
2. Cross-process: running the real serialization in subprocesses under
   several ``PYTHONHASHSEED`` values yields byte-identical output.

Why the obvious test is absent: building the same set twice in ONE process
and comparing the two ``list()`` results passes whether or not the code is
fixed — set iteration order does not depend on insertion order within a
single process. Only crossing processes exposes the bug.
"""

import json
import os
import subprocess
import sys
from collections import defaultdict

import coops.silver.collaboration_networks as collab
import coops.silver.members_statistics as ms

# Eight invented handles and eight invented repo slugs, deliberately fed in
# NON-alphabetical order. With >= 6 distinct strings the chance that set
# iteration accidentally matches sorted order is negligible.
REPOS = [
    "zeta-tests", "alpha-api", "nova-web", "delta-docs",
    "mango-cli", "quixote-tooling", "bolt-infra", "gravel-lint",
]
REPOS_SORTED = [
    "alpha-api", "bolt-infra", "delta-docs", "gravel-lint",
    "mango-cli", "nova-web", "quixote-tooling", "zeta-tests",
]

USERS = ["quixote", "mango", "zephyr", "albedo", "nova", "bolt", "gravel", "lumen"]
USERS_SORTED = ["albedo", "bolt", "gravel", "lumen", "mango", "nova", "quixote", "zephyr"]

HASH_SEEDS = ("0", "1", "2", "3", "4")


def _patch_module(monkeypatch, module, *, issues=None, prs=None, commits=None, events=None):
    """Wire fake load/save into a Silver module; return the saved artifacts."""
    data = {
        "issues": issues or [],
        "prs": prs or [],
        "commits": commits or [],
        "issue_events": events or [],
    }

    def fake_load(family):
        return data.get(family, [])

    saved = {}

    def fake_save(payload, path, timestamp=True):
        saved[path] = payload
        return path

    monkeypatch.setattr(module, "load_family", fake_load)
    monkeypatch.setattr(module, "save_json_data", fake_save)
    return saved


# ---------------------------------------------------------------------------
# In-process sortedness: explicit expected lists
# ---------------------------------------------------------------------------

class TestEmittedListsAreSorted:
    def test_members_statistics_repos(self, monkeypatch):
        """members_statistics.py:308 — `repos` comes from a `defaultdict(set)`
        and must be serialized sorted, not in set-iteration order."""
        commits = [
            {
                "commit": {"author": {"date": f"2024-01-{day:02d}T10:00:00Z"}},
                "author": {"login": "mango"},
                "repo_name": repo,
            }
            for day, repo in enumerate(REPOS, start=1)
        ]
        saved = _patch_module(monkeypatch, ms, commits=commits)
        ms.process_members_statistics()

        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        repos = stats[0]["repos"]
        assert repos == REPOS_SORTED
        assert repos == sorted(repos)
        assert stats[0]["repos_count"] == 8

    def test_collaboration_networks_user_collaborators(self, monkeypatch):
        """collaboration_networks.py:117 — each user's `collaborators` comes
        from a `defaultdict(set)` and must be serialized sorted."""
        issues = [{"repo_name": "alpha-api", "user": {"login": u}} for u in USERS]
        saved = _patch_module(monkeypatch, collab, issues=issues)
        collab.process_collaboration_networks()

        metrics = saved["data/silver/user_collaboration_metrics.json"]
        assert {m["user"] for m in metrics} == set(USERS)
        by_user = {m["user"]: m for m in metrics}
        for user, metric in by_user.items():
            expected = [u for u in USERS_SORTED if u != user]
            assert metric["collaborators"] == expected
            assert metric["collaborators"] == sorted(metric["collaborators"])

    def test_collaboration_networks_repo_contributors(self, monkeypatch):
        """collaboration_networks.py:132 — `contributors` in the repository
        analysis comes from a `defaultdict(set)` and must be serialized
        sorted."""
        issues = [{"repo_name": "alpha-api", "user": {"login": u}} for u in USERS]
        saved = _patch_module(monkeypatch, collab, issues=issues)
        collab.process_collaboration_networks()

        analysis = saved["data/silver/repository_collaboration_analysis.json"]
        assert len(analysis) == 1
        contributors = analysis[0]["contributors"]
        assert contributors == USERS_SORTED
        assert contributors == sorted(contributors)

    def test_collaboration_networks_edge_emission_order(self, monkeypatch):
        """collaboration_networks.py:75 — every edge in this fixture shares
        weight 1, so the final weight sort is stable and the emitted order is
        exactly the order edges were generated in, which is driven by the
        iteration order of the contributor set. Sorted contributors make the
        emitted edge sequence deterministic: lexicographic pair order."""
        issues = [{"repo_name": "alpha-api", "user": {"login": u}} for u in USERS]
        saved = _patch_module(monkeypatch, collab, issues=issues)
        collab.process_collaboration_networks()

        edges = saved["data/silver/collaboration_edges.json"]
        expected_pairs = [
            (a, b)
            for i, a in enumerate(USERS_SORTED)
            for b in USERS_SORTED[i + 1:]
        ]
        assert [(e["source"], e["target"]) for e in edges] == expected_pairs


# ---------------------------------------------------------------------------
# Cross-process determinism: the real property (byte-identical under
# different PYTHONHASHSEED values)
# ---------------------------------------------------------------------------

# Runs the real module against the bronze fixtures in the current working
# directory and prints the silver artifacts as JSON. The `_metadata`
# timestamp header written by save_json_data is stripped: it legitimately
# varies between runs and is not under test.
_MEMBERS_SNIPPET = """\
import contextlib, json, sys
with contextlib.redirect_stdout(sys.stderr):
    from coops.silver.members_statistics import process_members_statistics
    process_members_statistics()
with open("data/silver/members_statistics.json") as fh:
    records = json.load(fh)
if records and isinstance(records[0], dict) and "_metadata" in records[0]:
    records = records[1:]
print(json.dumps(records, sort_keys=True))
"""

_COLLAB_SNIPPET = """\
import contextlib, json, sys
with contextlib.redirect_stdout(sys.stderr):
    from coops.silver.collaboration_networks import process_collaboration_networks
    process_collaboration_networks()
out = {}
for name in ("collaboration_edges.json", "user_collaboration_metrics.json",
             "repository_collaboration_analysis.json",
             "cross_repository_hubs.json", "network_statistics.json"):
    with open(f"data/silver/{name}") as fh:
        records = json.load(fh)
    if isinstance(records, list):
        if records and isinstance(records[0], dict) and "_metadata" in records[0]:
            records = records[1:]
    elif isinstance(records, dict):
        records.pop("_metadata", None)
    out[name] = records
print(json.dumps(out, sort_keys=True))
"""


def _write_bronze(root, *, issues=(), prs=(), commits=(), events=()):
    """Write Bronze the way Silver reads it since #170: one file per
    repository, grouped out of the flat record sequences the tests build."""
    bronze = root / "data" / "bronze"
    bronze.mkdir(parents=True, exist_ok=True)
    for family, payload in (
        ("issues", issues),
        ("prs", prs),
        ("commits", commits),
        ("issue_events", events),
    ):
        by_repo = defaultdict(list)
        for record in payload:
            by_repo[record.get("repo_name", "unknown")].append(record)
        for repo, records in by_repo.items():
            (bronze / f"{family}_{repo}.json").write_text(
                json.dumps(records), encoding="utf-8"
            )


def _assert_identical_under_all_hash_seeds(snippet, cwd, must_contain):
    outs = {
        subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=cwd,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in HASH_SEEDS
    }
    assert len(outs) == 1, f"output varies by hash seed: {outs}"
    output = next(iter(outs))
    # Guard against an inert comparison: empty output would trivially match.
    assert must_contain in output


class TestCrossProcessDeterminism:
    def test_members_statistics_identical_across_hash_seeds(self, tmp_path):
        """members_statistics.json must be byte-identical across processes
        with different PYTHONHASHSEED values (#172)."""
        commits = [
            {
                "commit": {"author": {"date": f"2024-01-{day:02d}T10:00:00Z"}},
                "author": {"login": "mango"},
                "repo_name": repo,
            }
            for day, repo in enumerate(REPOS, start=1)
        ]
        issues = [
            {
                "user": {"login": "quixote"},
                "created_at": f"2024-02-{day:02d}T10:00:00Z",
                "repo_name": repo,
            }
            for day, repo in enumerate(REPOS[:4], start=1)
        ]
        _write_bronze(tmp_path, commits=commits, issues=issues)
        _assert_identical_under_all_hash_seeds(
            _MEMBERS_SNIPPET, tmp_path, must_contain="gravel-lint"
        )

    def test_collaboration_networks_identical_across_hash_seeds(self, tmp_path):
        """The collaboration artifacts must be byte-identical across
        processes with different PYTHONHASHSEED values (#172)."""
        membership = {
            "alpha-api": ["quixote", "mango", "zephyr"],
            "delta-docs": ["albedo", "nova", "bolt", "gravel"],
            "zeta-tests": ["lumen", "quixote", "albedo"],
        }
        issues = [
            {"repo_name": repo, "user": {"login": user}}
            for repo, users in membership.items()
            for user in users
        ]
        _write_bronze(tmp_path, issues=issues)
        _assert_identical_under_all_hash_seeds(
            _COLLAB_SNIPPET, tmp_path, must_contain="delta-docs"
        )
