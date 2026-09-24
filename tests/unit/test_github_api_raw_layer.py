"""Unit tests for the raw-layer integration in GitHubAPIClient.

The raw layer is a read-through/write-through cache in front of the API. These
tests fake only the two boundaries — ``requests.get`` and the ``RawStore`` —
and let the client and the Bronze commit extractor run for real.

The last class carries the binding guard from #111: the Bronze scrub must apply
on the raw-read path exactly as it does on the API paths, asserted by regex on
the serialised output with a control proving the probe finds the address in the
raw document first.
"""

import json
import re
from unittest.mock import Mock, patch

from coops.bronze.commits import extract_commits
from coops.domain import TenantId
from coops.storage.raw import RawDocument, raw_params_hash
from coops.utils.github_api import GitHubAPIClient

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _response(status_code, body=None, headers=None):
    resp = Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if body is not None:
        resp.json.return_value = body
    return resp


class InMemoryRawStore:
    """An in-memory ``RawStore`` for faking the storage boundary."""

    def __init__(self):
        self.docs = {}
        self.saved = []
        self.get_calls = []

    def put(self, tenant, provider, endpoint, params, payload, fetched_at=None):
        ph = raw_params_hash(params)
        doc = RawDocument(
            tenant_id=str(tenant),
            provider=provider,
            endpoint=endpoint,
            params_hash=ph,
            etag=None,
            fetched_at=fetched_at or "2024-01-01T00:00:00+00:00",
            payload=payload,
        )
        self.docs[(str(tenant), provider, endpoint, ph)] = doc
        return doc

    def save(self, tenant, provider, endpoint, params, etag, payload):
        ph = raw_params_hash(params)
        self.saved.append((str(tenant), provider, endpoint, ph, etag, payload))
        self.docs[(str(tenant), provider, endpoint, ph)] = RawDocument(
            tenant_id=str(tenant),
            provider=provider,
            endpoint=endpoint,
            params_hash=ph,
            etag=etag,
            fetched_at="2024-01-01T00:00:00+00:00",
            payload=payload,
        )

    def get(self, tenant, provider, endpoint, params):
        key = (str(tenant), provider, endpoint, raw_params_hash(params))
        self.get_calls.append(key)
        return self.docs.get(key)


class TestRawLayerClient:
    def test_fresh_document_short_circuits_network(self, tmp_path):
        store = InMemoryRawStore()
        tenant = TenantId("org")
        endpoint = "https://api.github.com/repos/org/repo"
        store.put(tenant, "github", endpoint, {}, {"id": 1})

        client = GitHubAPIClient(
            "token",
            cache_dir=str(tmp_path / "cache"),
            raw_store=store,
            tenant_id=tenant,
            raw_max_age_seconds=None,
        )

        with patch("requests.get") as mock_get:
            result = client.get_with_cache(endpoint)

        assert result == {"id": 1}
        mock_get.assert_not_called()
        assert client.cache_hits == 1
        assert client.cache_misses == 0

    def test_stale_document_falls_through_to_network(self, tmp_path):
        store = InMemoryRawStore()
        tenant = TenantId("org")
        endpoint = "https://api.github.com/repos/org/repo"
        store.put(
            tenant,
            "github",
            endpoint,
            {},
            {"id": "old"},
            fetched_at="2000-01-01T00:00:00+00:00",
        )

        client = GitHubAPIClient(
            "token",
            cache_dir=str(tmp_path / "cache"),
            raw_store=store,
            tenant_id=tenant,
            raw_max_age_seconds=60,
        )

        with patch("requests.get") as mock_get:
            mock_get.return_value = _response(200, body={"id": "new"})
            result = client.get_with_cache(endpoint)

        assert result == {"id": "new"}
        mock_get.assert_called_once()

    def test_fetched_payload_is_captured_to_raw_layer(self, tmp_path):
        store = InMemoryRawStore()
        tenant = TenantId("org")
        url = "https://api.github.com/repos/org/repo/commits?since=2024-01-01"
        body = [{"sha": "abc"}]

        client = GitHubAPIClient(
            "token",
            cache_dir=str(tmp_path / "cache"),
            raw_store=store,
            tenant_id=tenant,
            raw_max_age_seconds=None,
        )

        with patch("requests.get") as mock_get:
            mock_get.return_value = _response(
                200, body=body, headers={"ETag": '"etag-1"'}
            )
            result = client.get_with_cache(url)

        assert result == body
        assert len(store.saved) == 1
        tenant_str, provider, endpoint, ph, etag, payload = store.saved[0]
        assert tenant_str == "org"
        assert provider == "github"
        assert endpoint == "https://api.github.com/repos/org/repo/commits"
        assert ph == raw_params_hash({"since": "2024-01-01"})
        assert etag == '"etag-1"'
        assert payload == body

    def test_without_raw_store_network_is_unchanged(self, tmp_path):
        client = GitHubAPIClient("token", cache_dir=str(tmp_path / "cache"))
        with patch("requests.get") as mock_get:
            mock_get.return_value = _response(200, body={"id": 9})
            result = client.get_with_cache("https://api.github.com/repos/org/repo")
        assert result == {"id": 9}
        mock_get.assert_called_once()


class TestRawReadPathScrub:
    """The #111 guard: the Bronze scrub applies on the raw-read path too.

    The raw document *keeps* the address (that is what makes re-keying
    possible); the Bronze projection must not publish it. Asserted by regex on
    the serialised output — not by field names — with a control proving the
    probe finds the address in the raw document before sanitising.
    """

    def test_no_address_reaches_bronze_from_raw_read(self, tmp_path):
        tenant = TenantId("test-org")
        list_endpoint = "https://api.github.com/repos/test-org/repo1/commits"
        list_params = {"per_page": "50", "page": "1"}
        detail_endpoint = "https://api.github.com/repos/test-org/repo1/commits/abc123"

        # Unmodified REST commit list: raw author/committer emails, a message
        # body whose trailers carry addresses, and the signed verification
        # payload whose free text embeds the author as `Name <address>`.
        #
        # `commit.author.name` is deliberately NOT address-shaped: it is the
        # known open leak #132, out of scope here, so this test measures the
        # raw-read path rather than re-finding #132.
        raw_list = [
            {
                "sha": "abc123",
                "author": {"login": "linked_user", "id": 4242},
                "commit": {
                    "author": {
                        "name": "Linked Person",
                        "email": "linked@example.com",
                        "date": "2024-01-01T00:00:00Z",
                    },
                    "committer": {
                        "name": "Committer",
                        "email": "committer@example.com",
                        "date": "2024-01-01T00:00:01Z",
                    },
                    "message": "feat: thing\n\nCo-authored-by: Pair Person <pair@personal.example.net>\n",
                    "verification": {
                        "verified": True,
                        "reason": "valid",
                        "payload": (
                            "tree 0123456789abcdef\n"
                            "parent fedcba9876543210\n"
                            "author Linked Person <verified-author@example.net> 1700000000 +0000\n"
                        ),
                    },
                },
            }
        ]
        raw_details = {"stats": {"additions": 1, "deletions": 0, "total": 1}}

        store = InMemoryRawStore()
        store.put(tenant, "github", list_endpoint, list_params, raw_list)
        store.put(tenant, "github", detail_endpoint, {}, raw_details)

        client = GitHubAPIClient(
            "token",
            cache_dir=str(tmp_path / "cache"),
            raw_store=store,
            tenant_id=tenant,
            raw_max_age_seconds=None,
        )

        # CONTROL: the raw document the Bronze path will read still carries every
        # address, so the probe below is proven able to find them — a clean
        # result afterwards means the scrub ran, not that the probe was dead.
        raw_serialized = json.dumps(raw_list)
        for address in (
            "linked@example.com",
            "committer@example.com",
            "pair@personal.example.net",
            "verified-author@example.net",
        ):
            assert address in raw_serialized
            assert _EMAIL_RE.search(address)

        config = Mock()
        config.org_name = "test-org"
        repos = [{"name": "repo1", "full_name": "test-org/repo1"}]

        saved = []
        with (
            patch("coops.bronze.commits.load_json_data", return_value=repos),
            patch(
                "coops.bronze.commits.save_json_data",
                side_effect=lambda d, p, **k: saved.append(d) or p,
            ),
            patch("coops.utils.github_api.requests.get") as mock_get,
        ):
            mock_get.side_effect = AssertionError(
                "network must not be hit when the raw document is fresh"
            )
            extract_commits(client, config, method="rest")

        # The raw layer was actually consulted (the read path ran), and the
        # network was not.
        assert store.get_calls
        assert saved, "Bronze produced no output to assert on"

        serialized = json.dumps(saved[-1])
        assert not _EMAIL_RE.search(serialized)
        # Credit survives: the trailer and the human name stay.
        assert "Co-authored-by: Pair Person" in serialized
