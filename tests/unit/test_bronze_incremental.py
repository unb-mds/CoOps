"""Incremental-extraction behaviour (issue #110) at the Bronze extractor boundary.

These tests fake the HTTP client (the port) and the watermark store, and let the
real ``extract_issues`` / ``extract_commits`` / ``extract_repository_structure``
run, so the merge-by-number, append-by-id, ``since`` filtering and head-sha skip
logic are exercised for real rather than asserted against call sequences.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from coops.bronze.commits import extract_commits
from coops.bronze.issues import extract_issues
from coops.bronze.repository_structure import extract_repository_structure
from coops.bronze.watermarks import WatermarkStore

REPOS = [{"name": "repo1", "full_name": "org/repo1", "default_branch": "main"}]
_NOW = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)


def _store(path="unused.json", **fields):
    store = WatermarkStore(path, now=_NOW)
    if fields:
        store.update("org/repo1", **fields)
    return store


def _run_issues(client, files, watermarks):
    saved = {}

    def loader(path):
        return files.get(path)

    def saver(data, path, **kwargs):
        saved[path] = data
        return path

    with patch("coops.bronze.issues.load_json_data", side_effect=loader):
        with patch("coops.bronze.issues.save_json_data", side_effect=saver):
            extract_issues(client, MagicMock(), watermarks=watermarks)
    return saved


class TestIncrementalIssues:
    def test_since_param_added_from_watermark(self):
        client = MagicMock()
        client.get_paginated.return_value = []
        wm = _store(last_updated_at="2026-09-22T10:00:00Z")

        _run_issues(client, {"data/bronze/repositories_filtered.json": REPOS}, wm)

        issues_url = client.get_paginated.call_args_list[0][0][0]
        # One-second margin for GitHub's exclusive `since`.
        assert "since=2026-09-22T09:59:59Z" in issues_url

    def test_merge_keeps_prior_and_replaces_changed(self):
        client = MagicMock()
        fresh = [
            {"number": 1, "title": "changed", "updated_at": "2026-09-23T00:00:00Z"},
            {"number": 3, "title": "new", "updated_at": "2026-09-23T00:00:00Z"},
        ]
        client.get_paginated.side_effect = [fresh, []]
        prior_issues = [
            {"number": 1, "title": "old", "updated_at": "2026-09-22T00:00:00Z", "repo_name": "repo1"},
            {"number": 2, "title": "two", "updated_at": "2026-09-22T00:00:00Z", "repo_name": "repo1"},
        ]
        wm = _store(last_updated_at="2026-09-22T00:00:00Z")
        files = {
            "data/bronze/repositories_filtered.json": REPOS,
            "data/bronze/issues_repo1.json": [{"_metadata": {}}] + prior_issues,
        }

        saved = _run_issues(client, files, wm)

        merged = saved["data/bronze/issues_repo1.json"]
        assert [i["number"] for i in merged] == [1, 2, 3]
        by_number = {i["number"]: i for i in merged}
        assert by_number[1]["title"] == "changed"  # fresh replaces prior
        assert by_number[2]["title"] == "two"      # prior survives
        assert by_number[3]["title"] == "new"      # fresh added

    def test_events_append_only_newer_ids(self):
        client = MagicMock()
        client.get_paginated.return_value = []  # issues: none
        # Events come back newest-first; the fetch stops once it reaches id 2.
        client.get_with_cache.return_value = [
            {"id": 3, "event": "closed", "created_at": "2026-09-23T00:00:00Z", "actor": {"login": "u"}, "issue": {"number": 1}},
            {"id": 2, "event": "labeled", "created_at": "2026-09-22T12:00:00Z", "actor": {"login": "u"}, "issue": {"number": 1}},
        ]
        prior_events = [
            {"id": 1, "event": "opened", "created_at": "2026-09-22T10:00:00Z", "repo_name": "repo1", "actor": {"login": "u"}, "issue": {"number": 1}},
            {"id": 2, "event": "labeled", "created_at": "2026-09-22T12:00:00Z", "repo_name": "repo1", "actor": {"login": "u"}, "issue": {"number": 1}},
        ]
        wm = _store(last_event_id=2)
        files = {
            "data/bronze/repositories_filtered.json": REPOS,
            "data/bronze/issue_events_repo1.json": [{"_metadata": {}}] + prior_events,
        }

        saved = _run_issues(client, files, wm)

        events = saved["data/bronze/issue_events_repo1.json"]
        assert [e["id"] for e in events] == [1, 2, 3]
        # The events endpoint has no `since` filter: incrementality comes from
        # paging from the newest event and stopping at the boundary id.
        assert client.get_with_cache.call_count == 1
        assert client.get_with_cache.call_args[0][0].endswith("issues/events?per_page=100&page=1")

    def test_watermark_advances_to_new_maxes(self):
        client = MagicMock()
        fresh = [{"number": 9, "title": "x", "updated_at": "2026-09-24T00:00:00Z"}]
        client.get_paginated.return_value = fresh
        client.get_with_cache.return_value = [
            {"id": 99, "event": "closed", "created_at": "2026-09-24T00:00:00Z", "actor": {"login": "u"}, "issue": {"number": 9}}
        ]
        wm = _store(
            last_updated_at="2026-09-22T00:00:00Z",
            last_event_id=2,
        )
        files = {
            "data/bronze/repositories_filtered.json": REPOS,
            "data/bronze/issues_repo1.json": [{"_metadata": {}}],
            "data/bronze/prs_repo1.json": [{"_metadata": {}}],
            "data/bronze/issue_events_repo1.json": [{"_metadata": {}}],
        }

        _run_issues(client, files, wm)

        after = wm.get("org/repo1")
        assert after.last_updated_at == "2026-09-24T00:00:00Z"
        assert after.last_event_id == 99


def _run_commits(client, files, **kwargs):
    saved = {}

    def loader(path):
        return files.get(path)

    def saver(data, path, **kw):
        saved[path] = data
        return path

    with patch("coops.bronze.commits.load_json_data", side_effect=loader):
        with patch("coops.bronze.commits.save_json_data", side_effect=saver):
            extract_commits(client, MagicMock(), **kwargs)
    return saved


class TestIncrementalCommits:
    def test_watermark_since_bounds_graphql_and_disables_chunking(self):
        client = MagicMock()
        client.graphql_commit_history.return_value = ([], {})
        client.get_paginated.return_value = []
        wm = _store(last_run="2026-09-21T00:00:00Z")

        _run_commits(
            client,
            {"data/bronze/repositories_filtered.json": REPOS},
            method="graphql",
            watermarks=wm,
        )

        kwargs = client.graphql_commit_history.call_args[1]
        assert kwargs["since"] == "2026-09-21T00:00:00Z"
        # A short incremental window must not be split into time chunks.
        assert kwargs["split_large_extractions"] is False

    def test_new_commits_prepended_and_duplicates_dropped(self):
        client = MagicMock()
        client.get_paginated.return_value = [
            {
                "sha": "new1",
                "author": {"login": "a", "id": 1},
                "commit": {"author": {"name": "A", "email": "a@x.com", "date": "2026-09-23T00:00:00Z"}, "message": "new"},
            },
            {
                "sha": "old1",
                "author": {"login": "a", "id": 1},
                "commit": {"author": {"name": "A", "email": "a@x.com", "date": "2026-09-22T00:00:00Z"}, "message": "old"},
            },
        ]
        client.get_with_cache.return_value = {"stats": {"additions": 1, "deletions": 0, "total": 1}}
        prior = [
            {"sha": "old1", "author": {"login": "a", "id": 1}, "commit": {"author": {"name": "A", "date": "2026-09-22T00:00:00Z"}, "message": "old"}, "repo_name": "repo1", "parents": [], "additions": 1, "deletions": 0, "total_changes": 1},
            {"sha": "old2", "author": {"login": "b", "id": 2}, "commit": {"author": {"name": "B", "date": "2026-09-21T00:00:00Z"}, "message": "old2"}, "repo_name": "repo1", "parents": [], "additions": 1, "deletions": 0, "total_changes": 1},
        ]
        wm = _store(last_run="2026-09-23T00:00:00Z")
        files = {
            "data/bronze/repositories_filtered.json": REPOS,
            "data/bronze/commits_repo1.json": [{"_metadata": {}}] + prior,
        }

        saved = _run_commits(client, files, method="rest", watermarks=wm)

        merged = saved["data/bronze/commits_repo1.json"]
        assert [c["sha"] for c in merged] == ["new1", "old1", "old2"]


class TestIncrementalStructure:
    def test_skips_unchanged_head(self):
        client = MagicMock()
        client.get_with_cache.return_value = {"commit": {"sha": "abc123"}}
        wm = _store(head_shas={"main": "abc123"})
        files = {
            "data/bronze/repositories_filtered.json": REPOS,
            "data/bronze/structure_repo1.json": {"tree": [{"name": "a.py"}]},
        }

        with patch("coops.bronze.repository_structure.load_json_data", side_effect=files.get):
            with patch("coops.bronze.repository_structure.save_json_data", return_value="f"):
                result = extract_repository_structure(client, MagicMock(), watermarks=wm)

        assert "data/bronze/structure_repo1.json" in result
        client.get_repository_tree.assert_not_called()

    def test_fetches_and_records_when_head_changed(self):
        client = MagicMock()
        client.get_with_cache.return_value = {"commit": {"sha": "newsha"}}
        client.get_repository_tree.return_value = {
            "owner": "org", "repository": "repo1", "branch": "main",
            "sha": "newsha", "tree": [{"name": "b.py"}], "truncated": False,
            "method": "rest", "total_items": 1,
        }
        wm = _store(head_shas={"main": "oldsha"})
        files = {"data/bronze/repositories_filtered.json": REPOS}

        with patch("coops.bronze.repository_structure.load_json_data", side_effect=files.get):
            with patch("coops.bronze.repository_structure.save_json_data", return_value="f"):
                extract_repository_structure(client, MagicMock(), watermarks=wm)

        client.get_repository_tree.assert_called_once()
        assert wm.get("org/repo1").head_shas == {"main": "newsha"}
