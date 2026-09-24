"""Capture-mode tests for GitHubAPIClient (issue #109).

These fake the HTTP boundary only (``requests.get`` / ``requests.post``); the
capture writer runs for real, so the on-disk shape and permissions are
exercised end to end.
"""

import json
import os
import stat
from unittest.mock import Mock, patch

import pytest

from coops.utils.github_api import GitHubAPIClient


def _response(status_code, body=None, headers=None):
    resp = Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if body is not None:
        resp.json.return_value = body
    return resp


def _records(capture_dir, tenant_id):
    tenant_dir = os.path.join(capture_dir, tenant_id)
    if not os.path.isdir(tenant_dir):
        return []
    records = []
    for name in sorted(os.listdir(tenant_dir)):
        with open(os.path.join(tenant_dir, name), encoding="utf-8") as f:
            records.append(json.load(f))
    return records


def test_capture_requires_tenant_id(tmp_path):
    with pytest.raises(ValueError):
        GitHubAPIClient(token="t", capture_dir=str(tmp_path / "corpus-raw"))


def test_capture_off_by_default(tmp_path):
    client = GitHubAPIClient(token="t", cache_dir=str(tmp_path / "cache"))
    assert client._capture is None


def test_rest_200_is_captured_in_shape(tmp_path):
    capture_dir = str(tmp_path / "corpus-raw")
    client = GitHubAPIClient(
        token="t", cache_dir=str(tmp_path / "cache"), capture_dir=capture_dir, tenant_id="unb-mds"
    )
    url = "https://api.github.com/repos/unb-mds/coops/commits?per_page=50&page=2"
    body = {"commit": {"author": {"email": "jane@example.com"}}}

    with patch("requests.get") as mock_get:
        mock_get.return_value = _response(
            200,
            body=body,
            headers={"ETag": '"abc"', "X-RateLimit-Remaining": "4999", "X-RateLimit-Limit": "5000"},
        )
        client.get_with_cache(url)

    records = _records(capture_dir, "unb-mds")
    assert len(records) == 1
    rec = records[0]
    assert rec["tenant_id"] == "unb-mds"
    assert rec["provider"] == "github"
    assert rec["endpoint"] == "/repos/unb-mds/coops/commits"
    assert rec["params"] == {"per_page": "50", "page": "2"}
    assert rec["etag"] == '"abc"'
    assert rec["payload"] == body
    assert "fetched_at" in rec


def test_rest_capture_is_private(tmp_path):
    capture_dir = str(tmp_path / "corpus-raw")
    client = GitHubAPIClient(
        token="t", cache_dir=str(tmp_path / "cache"), capture_dir=capture_dir, tenant_id="unb-mds"
    )
    with patch("requests.get") as mock_get:
        mock_get.return_value = _response(200, body={"id": 1})
        client.get_with_cache("https://api.github.com/repos/x")

    tenant_dir = os.path.join(capture_dir, "unb-mds")
    assert stat.S_IMODE(os.stat(capture_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tenant_dir).st_mode) == 0o700
    for name in os.listdir(tenant_dir):
        assert stat.S_IMODE(os.stat(os.path.join(tenant_dir, name)).st_mode) == 0o600


def test_304_does_not_capture_again(tmp_path):
    capture_dir = str(tmp_path / "corpus-raw")
    client = GitHubAPIClient(
        token="t", cache_dir=str(tmp_path / "cache"), capture_dir=capture_dir, tenant_id="unb-mds"
    )
    url = "https://api.github.com/repos/unb-mds/coops"
    client._cache_set(url, {"id": 1})
    client._etag_set(url, '"abc"')

    with patch("requests.get") as mock_get:
        mock_get.return_value = _response(304, headers={"ETag": '"abc"'})
        client.get_with_cache(url)

    assert _records(capture_dir, "unb-mds") == []


def test_graphql_200_is_captured_in_shape(tmp_path):
    capture_dir = str(tmp_path / "corpus-raw")
    client = GitHubAPIClient(
        token="t", cache_dir=str(tmp_path / "cache"), capture_dir=capture_dir, tenant_id="unb-mds"
    )
    query = "query($owner: String!) { repository(owner: $owner) { name } }"
    variables = {"owner": "unb-mds"}
    body = {"data": {"repository": {"name": "coops", "owner": {"email": "jane@example.com"}}}}

    with patch("requests.post") as mock_post:
        mock_post.return_value = _response(200, body=body)
        client.graphql(query, variables, use_cache=False)

    records = _records(capture_dir, "unb-mds")
    assert len(records) == 1
    rec = records[0]
    assert rec["endpoint"] == "graphql"
    assert rec["params"]["query"] == query
    assert rec["params"]["variables"] == variables
    assert rec["etag"] is None
    assert rec["payload"] == body
