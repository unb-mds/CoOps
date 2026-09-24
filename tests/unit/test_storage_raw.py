"""Unit tests for coops.storage.raw — the tenant-scoped raw layer.

The Mongo implementation is exercised against a fake pymongo collection (the
port boundary), so the query building and tenant scoping run for real without a
database. The real MongoDB is covered by tests/integration/test_mongo_raw_store.py.
"""

import pytest

from coops.domain import TenantId
from coops.storage.raw import (
    INDEX_NAME,
    MongoRawStore,
    is_fresh,
    raw_params_hash,
)


def _key(filter_):
    return tuple(sorted((k, str(v)) for k, v in filter_.items()))


class FakeCollection:
    """Minimal in-memory stand-in for a pymongo collection.

    ``find_one`` matches on a *prefix* of the stored fields, like MongoDB's
    equality query does, so removing a field from the filter really does widen
    the match (which is exactly how a dropped tenant filter would leak).
    """

    def __init__(self):
        self._docs = {}
        self.indexes = []
        self.find_filters = []
        self.replace_filters = []

    def create_index(self, spec, **kwargs):
        self.indexes.append((list(spec), kwargs))

    def replace_one(self, filter_, replacement, upsert=False):
        self.replace_filters.append(dict(filter_))
        self._docs[_key(filter_)] = dict(replacement)

    def find_one(self, filter_):
        self.find_filters.append(dict(filter_))
        wanted = {k: str(v) for k, v in filter_.items()}
        for key_tuple, doc in self._docs.items():
            key_dict = dict(key_tuple)
            if all(key_dict.get(k) == v for k, v in wanted.items()):
                return doc
        return None


class FakeClient:
    """A pymongo-client stand-in: `client[db][collection]` resolves to the collection."""

    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, key):
        return _FakeDatabase(self._collection)


class _FakeDatabase:
    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, key):
        return self._collection


def _store(collection):
    return MongoRawStore(client=FakeClient(collection))


class TestRawParamsHash:
    def test_stable_and_order_independent(self):
        assert raw_params_hash({"a": "1", "b": "2"}) == raw_params_hash(
            {"b": "2", "a": "1"}
        )

    def test_none_and_empty_are_equal(self):
        assert raw_params_hash(None) == raw_params_hash({})

    def test_different_params_differ(self):
        assert raw_params_hash({"page": "1"}) != raw_params_hash({"page": "2"})


class TestIsFresh:
    def test_none_max_age_is_always_fresh(self):
        assert is_fresh("2000-01-01T00:00:00+00:00", None)

    def test_within_window(self):
        assert is_fresh(
            "2024-01-01T00:00:00+00:00", 60, now=_dt("2024-01-01T00:00:30+00:00")
        )

    def test_outside_window(self):
        assert not is_fresh(
            "2024-01-01T00:00:00+00:00", 60, now=_dt("2024-01-01T00:01:01+00:00")
        )

    def test_malformed_timestamp_is_never_fresh(self):
        assert not is_fresh("not-a-date", 60)


class TestMongoRawStore:
    def test_save_writes_document_shape_and_scopes_filter(self):
        collection = FakeCollection()
        store = _store(collection)

        store.save(
            TenantId("UnB-Mds"),
            "github",
            "https://api.github.com/repos/x/y/commits",
            {"since": "2024"},
            '"etag-1"',
            [{"sha": "abc"}],
        )

        assert len(collection._docs) == 1
        # The tenant's slug is stored verbatim: since #92 TenantId does no
        # case folding, so the raw layer keys on exactly the assigned slug.
        assert (
            _key(
                {
                    "tenant_id": "UnB-Mds",
                    "provider": "github",
                    "endpoint": "https://api.github.com/repos/x/y/commits",
                    "params_hash": raw_params_hash({"since": "2024"}),
                }
            )
            in collection._docs
        )
        doc = next(iter(collection._docs.values()))
        assert doc["payload"] == [{"sha": "abc"}]
        assert doc["etag"] == '"etag-1"'
        assert doc["fetched_at"]
        # The replace filter is the full 4-tuple, tenant included.
        assert collection.replace_filters[0]["tenant_id"] == "UnB-Mds"

    def test_get_always_injects_tenant_filter(self):
        collection = FakeCollection()
        store = _store(collection)

        store.save(
            TenantId("org-a"), "github", "https://api.github.com/e", {}, None, {"v": 1}
        )
        store.save(
            TenantId("org-b"), "github", "https://api.github.com/e", {}, None, {"v": 2}
        )

        doc = store.get(TenantId("org-a"), "github", "https://api.github.com/e", {})
        assert doc is not None
        assert doc.payload == {"v": 1}
        assert doc.tenant_id == "org-a"

        other = store.get(TenantId("org-b"), "github", "https://api.github.com/e", {})
        assert other is not None
        assert other.payload == {"v": 2}

        # Every find carried a tenant_id filter; none could be issued without it.
        assert all("tenant_id" in f for f in collection.find_filters)
        assert all(
            f["tenant_id"] in ("org-a", "org-b") for f in collection.find_filters
        )

    def test_tenant_isolation_no_cross_read(self):
        collection = FakeCollection()
        store = _store(collection)

        store.save(
            TenantId("org-a"),
            "github",
            "https://api.github.com/e",
            {},
            None,
            {"secret": "a"},
        )

        # Same endpoint/params, different tenant -> no document.
        assert (
            store.get(TenantId("org-b"), "github", "https://api.github.com/e", {})
            is None
        )

    def test_get_missing_returns_none(self):
        store = _store(FakeCollection())
        assert (
            store.get(TenantId("org-a"), "github", "https://api.github.com/e", {})
            is None
        )

    def test_index_is_created_on_the_four_tuple(self):
        collection = FakeCollection()
        _store(collection)
        assert len(collection.indexes) == 1
        spec, kwargs = collection.indexes[0]
        assert spec == [
            ("tenant_id", 1),
            ("provider", 1),
            ("endpoint", 1),
            ("params_hash", 1),
        ]
        assert kwargs["unique"] is True
        assert kwargs["name"] == INDEX_NAME

    def test_requires_uri_or_client(self):
        with pytest.raises(ValueError):
            MongoRawStore()


def _dt(iso):
    from datetime import datetime

    return datetime.fromisoformat(iso)
