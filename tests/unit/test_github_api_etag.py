"""ETag conditional-request tests for GitHubAPIClient.get_with_cache.

These fake the HTTP boundary only (``requests.get``); no internal cache or
client methods are patched, so the on-disk cache format and the request path
are exercised for real.
"""

import json
import os
from unittest.mock import Mock, patch

from coops.utils.github_api import GitHubAPIClient


def _response(status_code, body=None, headers=None):
    """Build a fake ``requests`` response object."""
    resp = Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if body is not None:
        resp.json.return_value = body
    return resp


def test_304_serves_cached_body(tmp_path):
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"))
    url = "https://api.github.com/repos/test/repo"
    body = {"id": 1, "name": "test"}
    etag = '"abc123"'

    client._cache_set(url, body)
    client._etag_set(url, etag)

    with patch("requests.get") as mock_get:
        mock_get.return_value = _response(
            304,
            headers={"ETag": etag, "X-RateLimit-Remaining": "4999", "X-RateLimit-Limit": "5000"},
        )
        result = client.get_with_cache(url)

    assert result == body
    # A conditional request was sent, not a bare GET.
    assert mock_get.call_args.kwargs["headers"]["If-None-Match"] == etag
    # A 304 is served from cache and does not count as a miss.
    assert client.cache_hits == 1
    assert client.cache_misses == 0
    assert client.last_rate_limit["remaining"] == 4999


def test_200_replaces_cached_body(tmp_path):
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"))
    url = "https://api.github.com/repos/test/repo"

    client._cache_set(url, {"id": 1})
    client._etag_set(url, '"old-etag"')

    new_body = {"id": 2}
    with patch("requests.get") as mock_get:
        mock_get.return_value = _response(
            200,
            body=new_body,
            headers={"ETag": '"new-etag"', "X-RateLimit-Remaining": "4998", "X-RateLimit-Limit": "5000"},
        )
        result = client.get_with_cache(url)

    assert result == new_body
    # Body and ETag were both replaced on disk.
    assert client._cache_get(url) == new_body
    assert client._etag_get(url) == '"new-etag"'
    assert client.cache_misses == 1
    assert client.cache_hits == 0


def test_missing_etag_falls_back_to_cached_body(tmp_path, capsys):
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"))
    url = "https://api.github.com/repos/test/repo"
    body = {"id": 1}

    # First fetch: a 200 with no ETag header -> body cached, no ETag stored.
    with patch("requests.get") as mock_get:
        mock_get.return_value = _response(
            200,
            body=body,
            headers={"X-RateLimit-Remaining": "4999", "X-RateLimit-Limit": "5000"},
        )
        client.get_with_cache(url)

    assert client._etag_get(url) is None

    # Second fetch: warm body with no ETag cannot be revalidated, so it is
    # served directly without touching the network.
    with patch("requests.get") as mock_get2:
        result = client.get_with_cache(url)

    assert result == body
    mock_get2.assert_not_called()
    assert "Using cached data" in capsys.readouterr().out


def test_legacy_cache_entry_still_readable(tmp_path):
    client = GitHubAPIClient(token="test", cache_dir=str(tmp_path / "cache"))
    url = "https://api.github.com/repos/test/repo"
    body = [{"id": 1}, {"id": 2}]

    # Simulate an entry written by the pre-ETag format: the raw JSON body at
    # cache/<md5(url)>.json, with no .etag sidecar.
    os.makedirs(client.cache_dir, exist_ok=True)
    cache_file = os.path.join(client.cache_dir, client._get_cache_key(url))
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(body, f)

    result = client.get_with_cache(url)

    assert result == body
    assert client.cache_hits == 1
    assert client.cache_misses == 0
