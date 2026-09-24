"""The Silver→Bronze read path after #170: per-repository files, not aggregates.

A data migration needs two assertions, not one. *Nothing was lost*: records
in the per-repository files reach every Silver processor. *Nothing was
wrongly changed*: records that exist only in the ``_all`` aggregate (or in a
derived ``_with_stats`` copy beside the original) reach none of them — the
aggregate and the derived copy both restate per-repo content, so counting
either on top is a double count, and reading the aggregate *instead* would
resurrect the retired file this migration exists to remove.

The processors under test run for real against a Bronze tree on disk; only
``save_json_data`` is captured, so the artifacts asserted on are the ones
the pipeline would publish.
"""

import json

import pytest

import coops.silver.collaboration_networks as collab
import coops.silver.contribution_metrics as contrib
import coops.silver.members_statistics as ms
import coops.silver.temporal_analysis as temporal
from coops.silver.bronze_input import load_family

ALICE_ISSUE = {
    "user": {"login": "alice"},
    "created_at": "2024-01-02T10:00:00Z",
    "updated_at": "2024-01-02T10:00:00Z",
    "state": "open",
    "repo_name": "alpha",
}
#: A record that exists ONLY in the aggregate. If any processor ever reads
#: ``issues_all.json`` again, this login shows up in its output.
GHOST_ISSUE = {
    "user": {"login": "aggregate-ghost"},
    "created_at": "2024-01-03T10:00:00Z",
    "updated_at": "2024-01-03T10:00:00Z",
    "state": "open",
    "repo_name": "ghost-repo",
}


def _write_poisoned_bronze(tmp_path):
    """A Bronze tree where wrong reads are visible.

    * ``issues_alpha.json`` — the one real record.
    * ``issues_alpha_with_stats.json`` — a derived copy of it; reading both
      counts alice twice.
    * ``issues_all.json`` — the aggregate: alice's record (as a faithful
      concatenation would include it) plus a ghost record that exists
      nowhere else.
    """
    bronze = tmp_path / "data" / "bronze"
    bronze.mkdir(parents=True)
    (bronze / "issues_alpha.json").write_text(json.dumps([ALICE_ISSUE]))
    (bronze / "issues_alpha_with_stats.json").write_text(json.dumps([ALICE_ISSUE]))
    (bronze / "issues_all.json").write_text(
        json.dumps([ALICE_ISSUE, GHOST_ISSUE])
    )


def _capture_save(monkeypatch, module):
    saved = {}

    def fake_save(data, path, timestamp=True):
        saved[path] = data
        return path

    monkeypatch.setattr(module, "save_json_data", fake_save)
    return saved


class TestLoadFamily:
    def test_reads_per_repository_files_in_filename_order(self, monkeypatch, tmp_path):
        bronze = tmp_path / "data" / "bronze"
        bronze.mkdir(parents=True)
        (bronze / "issues_beta.json").write_text(json.dumps(
            [{"_metadata": {"file_path": "x"}}, {"user": {"login": "bob"}}]
        ))
        (bronze / "issues_alpha.json").write_text(json.dumps(
            [{"user": {"login": "alice"}}]
        ))
        monkeypatch.chdir(tmp_path)

        records = load_family("issues")

        # The `_metadata` header is stripped per file; files are enumerated
        # sorted by filename, so the output order is stable.
        assert [r["user"]["login"] for r in records] == ["alice", "bob"]

    def test_aggregate_and_derived_copies_are_not_read(self, monkeypatch, tmp_path):
        _write_poisoned_bronze(tmp_path)
        monkeypatch.chdir(tmp_path)

        records = load_family("issues")

        assert records == [ALICE_ISSUE]

    def test_document_family_rejected(self):
        """``structure`` files are single documents; iterating them yields
        their keys — the confident zero this loader must not return."""
        with pytest.raises(ValueError, match="unknown bronze record family"):
            load_family("structure")


def test_contribution_metrics_reads_per_repo_only(monkeypatch, tmp_path):
    _write_poisoned_bronze(tmp_path)
    monkeypatch.chdir(tmp_path)
    saved = _capture_save(monkeypatch, contrib)

    contrib.process_contribution_metrics()

    users = {c["user"]: c for c in saved["data/silver/contribution_metrics.json"]}
    assert users["alice"]["issues_created"] == 1
    assert "aggregate-ghost" not in users
    repos = {r["repo"]: r for r in saved["data/silver/repository_metrics.json"]}
    assert repos["alpha"]["issues"] == 1
    assert "ghost-repo" not in repos


def test_members_statistics_reads_per_repo_only(monkeypatch, tmp_path):
    _write_poisoned_bronze(tmp_path)
    monkeypatch.chdir(tmp_path)
    saved = _capture_save(monkeypatch, ms)

    ms.process_members_statistics()

    stats = saved["data/silver/members_statistics.json"]
    ids = {s["id"] for s in stats}
    assert ids == {"alice"}
    by_id = {s["id"]: s for s in stats}
    assert by_id["alice"]["total_issues_created"] == 1


def test_collaboration_networks_reads_per_repo_only(monkeypatch, tmp_path):
    _write_poisoned_bronze(tmp_path)
    # user_collaboration_metrics only lists users that share a repository
    # with someone, so this test needs a second real contributor.
    bob_issue = dict(ALICE_ISSUE, user={"login": "bob"})
    (tmp_path / "data" / "bronze" / "issues_alpha.json").write_text(
        json.dumps([ALICE_ISSUE, bob_issue])
    )
    monkeypatch.chdir(tmp_path)
    saved = _capture_save(monkeypatch, collab)

    collab.process_collaboration_networks()

    metrics = saved["data/silver/user_collaboration_metrics.json"]
    assert {m["user"] for m in metrics} == {"alice", "bob"}
    analysis = saved["data/silver/repository_collaboration_analysis.json"]
    assert {r["repo"] for r in analysis} == {"alpha"}


def test_temporal_analysis_reads_per_repo_only(monkeypatch, tmp_path):
    _write_poisoned_bronze(tmp_path)
    monkeypatch.chdir(tmp_path)
    saved = _capture_save(monkeypatch, temporal)

    temporal.process_temporal_analysis()

    events = saved["data/silver/temporal_events.json"]
    assert [e["user"] for e in events] == ["alice"]
    assert events[0]["type"] == "issue_created"
