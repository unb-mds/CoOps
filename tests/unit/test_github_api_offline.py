"""Offline-mode tests for GitHubAPIClient.

Offline mode is a guarantee about the *source* of every response: the cache,
or the run stops. These fake the HTTP boundary only (``requests.get`` /
``requests.post``) with a transport that records its calls — a green test
that never asserted on the call count would prove nothing here, because the
failure mode (#199) is precisely a response quietly coming from somewhere
other than the cache.
"""

import os
from unittest.mock import Mock, patch

import pytest

from coops.utils.github_api import GitHubAPIClient, OfflineCacheMiss


def _no_network():
    """A stubbed transport whose only job is to fail if it is ever reached.

    ``side_effect`` raises inside the client, so a code path that tries the
    network is caught red-handed rather than returning a plausible body.
    """
    return Mock(side_effect=AssertionError("offline mode must not touch the network"))


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def test_offline_serves_etagged_body_without_network(tmp_path):
    """The whole point: an ETag'd entry must NOT go to revalidation.

    Online, a warm entry with an ETag sends ``If-None-Match``; with the
    network merely blocked, the ``RequestException`` path returns None and
    the record silently disappears from the replay (#199). Offline, the body
    is served from the cache with no request at all.
    """
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"), offline=True)
    url = "https://api.github.com/repos/test-org/repo/issues?per_page=100&page=1"
    body = [{"number": 1}, {"number": 2}]

    client._cache_set(url, body)
    client._etag_set(url, '"etag-1"')
    cache_file = os.path.join(client.cache_dir, client._get_cache_key(url))
    etag_file = client._get_etag_path(url)
    before = (_read(cache_file), _read(etag_file))

    transport = _no_network()
    with patch("requests.get", transport):
        result = client.get_with_cache(url)

    assert result == body
    assert transport.call_count == 0
    assert client.cache_hits == 1
    assert client.cache_misses == 0
    # No write: body and ETag sidecar are byte-identical after the offline read.
    assert (_read(cache_file), _read(etag_file)) == before


def test_offline_cache_miss_raises_naming_the_url(tmp_path):
    """A miss must stop the run, and the message must name the URL so the
    missing cache entry can be identified."""
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"), offline=True)
    url = "https://api.github.com/orgs/test-org/repos?per_page=100&page=1"

    transport = _no_network()
    with patch("requests.get", transport):
        with pytest.raises(OfflineCacheMiss) as exc_info:
            client.get_with_cache(url)

    assert url in str(exc_info.value)
    assert exc_info.value.url == url
    assert transport.call_count == 0


def test_offline_serves_cache_even_when_use_cache_false(tmp_path):
    """``use_cache=False`` asks for a refresh, which offline cannot honour.

    The cached body is served anyway — the cache is the only source — rather
    than failing a replay whose entries are all present but whose operator
    forgot ``--cache``.
    """
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"), offline=True)
    url = "https://api.github.com/repos/test-org/repo"
    body = {"id": 1}

    client._cache_set(url, body)

    transport = _no_network()
    with patch("requests.get", transport):
        result = client.get_with_cache(url, use_cache=False)

    assert result == body
    assert transport.call_count == 0


def test_offline_miss_with_use_cache_false_also_raises(tmp_path):
    """Either way of asking, a body that is not in the cache cannot be
    produced offline, so the run stops."""
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"), offline=True)

    with patch("requests.get", _no_network()):
        with pytest.raises(OfflineCacheMiss):
            client.get_with_cache("https://api.github.com/repos/test-org/repo", use_cache=False)


def test_offline_graphql_serves_cache_without_network(tmp_path):
    """The GraphQL path carries the same guarantee as REST: a cached response
    is served with no POST. The cache is warmed through the real online write
    path first, so the offline lookup is proven to use the identical key
    derivation rather than a copy of it."""
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"))
    query = "query { viewer { login } }"
    body = {"data": {"viewer": {"login": "someone"}}}

    response = Mock()
    response.status_code = 200
    response.json.return_value = body
    with patch("requests.post", return_value=response):
        assert client.graphql(query) == body

    client.offline = True
    cache_files = sorted(os.listdir(client.cache_dir))

    transport = _no_network()
    with patch("requests.post", transport):
        result = client.graphql(query)

    assert result == body
    assert transport.call_count == 0
    # No write: the cache directory still holds exactly the entry the online
    # warm-up wrote.
    assert sorted(os.listdir(client.cache_dir)) == cache_files


def test_offline_graphql_miss_raises_naming_the_endpoint(tmp_path):
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"), offline=True)

    transport = _no_network()
    with patch("requests.post", transport):
        with pytest.raises(OfflineCacheMiss) as exc_info:
            client.graphql("query { rateLimit { remaining } }")

    assert "https://api.github.com/graphql" in str(exc_info.value)
    assert transport.call_count == 0


def test_offline_miss_not_swallowed_by_repository_tree(tmp_path):
    """get_repository_tree turns arbitrary failures into an empty-tree
    payload; an offline miss must still stop the run instead of being
    reported as one failed repository (#199)."""
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"), offline=True)

    with patch("requests.get", _no_network()):
        with pytest.raises(OfflineCacheMiss):
            client.get_repository_tree("test-org", "repo")


def test_offline_miss_not_swallowed_by_parallel_commit_fetch(tmp_path):
    """A commit-detail miss inside a fetch worker must not be logged as a
    dropped commit: that is exactly the silent partial answer offline mode
    exists to prevent."""
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"), offline=True)

    with patch("requests.get", _no_network()):
        with pytest.raises(OfflineCacheMiss):
            client._fetch_rest_commit_details_parallel(
                [{"sha": "abc123"}], "test-org", "repo", use_cache=True
            )
