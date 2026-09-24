"""Tests for coops/bronze/repository_structure.py — extract_repository_structure."""

from unittest.mock import MagicMock
import pytest
import coops.bronze.repository_structure as rs


def _setup(monkeypatch, *, filtered_repos=None):
    """Wire up fake load/save and return (client_mock, config_mock, saved_dict)."""
    saved = {}

    def fake_load(path):
        if "repositories_filtered" in path:
            return filtered_repos
        return None

    def fake_save(data, path, timestamp=False):
        saved[path] = data
        return path

    monkeypatch.setattr(rs, "load_json_data", fake_load)
    monkeypatch.setattr(rs, "save_json_data", fake_save)

    client = MagicMock()
    config = MagicMock()
    return client, config, saved


# ---------------------------------------------------------------------------
# No repos / empty input
# ---------------------------------------------------------------------------

class TestNoRepos:
    def test_no_filtered_repos(self, monkeypatch):
        client, config, saved = _setup(monkeypatch, filtered_repos=None)
        result = rs.extract_repository_structure(client, config)
        assert result == []

    def test_empty_filtered_repos(self, monkeypatch):
        client, config, saved = _setup(monkeypatch, filtered_repos=[])
        result = rs.extract_repository_structure(client, config)
        assert result == []


# ---------------------------------------------------------------------------
# Metadata stripping
# ---------------------------------------------------------------------------

class TestMetadataHandling:
    def test_metadata_stripped(self, monkeypatch):
        repos = [
            {"_metadata": {"ts": "2024-01-01"}},
            {"name": "r1", "full_name": "org/r1", "default_branch": "main"},
        ]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.return_value = {
            "tree": [{"name": "a.py", "type": "file"}],
            "truncated": False,
            "method": "rest",
        }
        result = rs.extract_repository_structure(client, config)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Offline replay misses
# ---------------------------------------------------------------------------

class TestOfflineCacheMiss:
    def test_offline_miss_stops_the_run(self, monkeypatch):
        """A cache miss in offline mode must propagate: the per-repo except
        would otherwise count the repository as failed, continue the run and
        report a plausible-looking partial replay (#199)."""
        repos = [{"name": "r1", "full_name": "org/r1", "default_branch": "main"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.side_effect = rs.OfflineCacheMiss(
            "https://api.github.com/repos/org/r1/branches/main"
        )

        with pytest.raises(rs.OfflineCacheMiss):
            rs.extract_repository_structure(client, config)


# ---------------------------------------------------------------------------
# Invalid repo entries
# ---------------------------------------------------------------------------

class TestInvalidRepos:
    def test_none_entry_skipped(self, monkeypatch):
        repos = [None, {"name": "r1", "full_name": "org/r1", "default_branch": "main"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.return_value = {
            "tree": [{"name": "a.py", "type": "file"}],
            "truncated": False,
        }
        result = rs.extract_repository_structure(client, config)
        assert len(result) == 1

    def test_non_dict_entry_skipped(self, monkeypatch):
        repos = ["not-a-dict"]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        result = rs.extract_repository_structure(client, config)
        assert result == []

    def test_missing_full_name_slash(self, monkeypatch):
        """full_name without '/' is skipped."""
        repos = [{"name": "r1", "full_name": "noSlash", "default_branch": "main"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        result = rs.extract_repository_structure(client, config)
        assert result == []


# ---------------------------------------------------------------------------
# REST success
# ---------------------------------------------------------------------------

class TestRESTSuccess:
    def test_basic_extraction(self, monkeypatch):
        repos = [{"name": "myrepo", "full_name": "org/myrepo", "default_branch": "main"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.return_value = {
            "tree": [
                {"name": "main.py", "type": "file"},
                {"name": "util.py", "type": "file"},
            ],
            "truncated": False,
            "method": "rest",
        }

        result = rs.extract_repository_structure(client, config)
        assert len(result) == 1
        assert "structure_myrepo" in result[0]
        data = saved[result[0]]
        assert len(data["tree"]) == 2
        assert data["repository_metadata"]["full_name"] == "org/myrepo"


# ---------------------------------------------------------------------------
# REST truncated → GraphQL fallback
# ---------------------------------------------------------------------------

class TestGraphQLFallback:
    def test_truncated_falls_back_to_graphql(self, monkeypatch):
        repos = [{"name": "big", "full_name": "org/big", "default_branch": "main"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.return_value = {"tree": [], "truncated": True}
        client.graphql_repository_tree.return_value = {
            "tree": [{"name": "a.py", "type": "file"}],
            "method": "graphql",
        }

        result = rs.extract_repository_structure(client, config)
        assert len(result) == 1
        client.graphql_repository_tree.assert_called_once()


# ---------------------------------------------------------------------------
# Empty / missing tree
# ---------------------------------------------------------------------------

class TestEmptyTree:
    def test_no_tree_key(self, monkeypatch):
        repos = [{"name": "empty", "full_name": "org/empty", "default_branch": "main"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.return_value = {"truncated": False}
        result = rs.extract_repository_structure(client, config)
        assert result == []

    def test_empty_tree_list(self, monkeypatch):
        repos = [{"name": "empty", "full_name": "org/empty", "default_branch": "main"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.return_value = {"tree": [], "truncated": False}
        result = rs.extract_repository_structure(client, config)
        assert result == []

    def test_none_structure(self, monkeypatch):
        repos = [{"name": "bad", "full_name": "org/bad", "default_branch": "main"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.return_value = None
        result = rs.extract_repository_structure(client, config)
        assert result == []


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------

class TestExceptionHandling:
    def test_api_exception_continues(self, monkeypatch):
        repos = [
            {"name": "fail", "full_name": "org/fail", "default_branch": "main"},
            {"name": "ok", "full_name": "org/ok", "default_branch": "main"},
        ]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)

        call_count = [0]
        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("API down")
            return {"tree": [{"name": "a.py", "type": "file"}], "truncated": False}

        client.get_repository_tree.side_effect = side_effect
        result = rs.extract_repository_structure(client, config)
        # First repo fails, second succeeds
        assert len(result) == 1
        assert "structure_ok" in result[0]


# ---------------------------------------------------------------------------
# Multiple repos (success + failure counting)
# ---------------------------------------------------------------------------

class TestMultipleRepos:
    def test_mixed_success_failure(self, monkeypatch):
        repos = [
            {"name": "r1", "full_name": "org/r1", "default_branch": "main"},
            {"name": "r2", "full_name": "org/r2", "default_branch": "dev"},
            {"name": "r3", "full_name": "org/r3", "default_branch": "main"},
        ]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)

        def tree_response(**kwargs):
            if kwargs.get("repo") == "r2":
                return {"tree": [], "truncated": False}  # empty → fail
            return {
                "tree": [{"name": "f.py", "type": "file"}],
                "truncated": False,
                "method": "rest",
            }

        client.get_repository_tree.side_effect = tree_response
        result = rs.extract_repository_structure(client, config)
        assert len(result) == 2  # r1 and r3 succeed

    def test_default_branch_respected(self, monkeypatch):
        repos = [{"name": "r", "full_name": "org/r", "default_branch": "develop"}]
        client, config, saved = _setup(monkeypatch, filtered_repos=repos)
        client.get_repository_tree.return_value = {
            "tree": [{"name": "a.py", "type": "file"}], "truncated": False,
        }
        rs.extract_repository_structure(client, config)
        client.get_repository_tree.assert_called_once_with(
            owner="org", repo="r", branch="develop", use_cache=True
        )
