"""Unit tests for coops.storage.datasets — the StoragePort Mongo adapter (#39).

The adapter is exercised against a fake pymongo collection (the port
boundary), so the query building and tenant scoping run for real without a
database and without a network. The port-level behaviours — round-trip,
replace-not-append, tenant isolation on load and list, the address
validators — are held by subclassing ``StoragePortContract``, the same
contract the reference in-memory implementation answers to in
``test_storage_port.py``. The classes here add the Mongo-specific facts:
the document shape, the unique ``(tenant_id, layer, entity)`` index, the
replace-not-append upsert at the collection level, and the fact that the
tenant term is injected by the adapter rather than read from the data.
"""

from __future__ import annotations

import pytest

from coops.domain import TenantId
from coops.storage.datasets import INDEX_NAME, MongoStorageAdapter
from tests.unit.test_storage_port import StoragePortContract


def _key(filter_):
    return tuple(sorted((k, str(v)) for k, v in filter_.items()))


class FakeCollection:
    """Minimal in-memory stand-in for a pymongo collection.

    ``find_one``/``find``/``count_documents`` match on a *subset* of the
    stored key fields, like MongoDB's equality query does, so removing a
    field from the filter really does widen the match (which is exactly how
    a dropped tenant filter would leak).
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

    def _matching_keys(self, filter_):
        wanted = {k: str(v) for k, v in filter_.items()}
        for key_tuple in self._docs:
            key_dict = dict(key_tuple)
            if all(key_dict.get(k) == v for k, v in wanted.items()):
                yield key_tuple

    def find_one(self, filter_):
        self.find_filters.append(dict(filter_))
        for key_tuple in self._matching_keys(filter_):
            return self._docs[key_tuple]
        return None

    def find(self, filter_, projection=None):
        self.find_filters.append(dict(filter_))
        return [dict(self._docs[k]) for k in self._matching_keys(filter_)]

    def count_documents(self, filter_):
        return sum(1 for _ in self._matching_keys(filter_))


class FakeClient:
    """A pymongo-client stand-in: `client[db][collection]` resolves to the collection."""

    def __init__(self, collection):
        self._collection = collection
        self.closed = False

    def __getitem__(self, key):
        return _FakeDatabase(self._collection)

    def close(self):
        self.closed = True


class _FakeDatabase:
    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, key):
        return self._collection


def _adapter(collection):
    return MongoStorageAdapter(client=FakeClient(collection))


class TestMongoStorageAdapter(StoragePortContract):
    """#39's adapter, held to the contract every StoragePort implementation must hold."""

    def make_store(self) -> MongoStorageAdapter:
        return _adapter(FakeCollection())

    def test_the_adapter_satisfies_the_protocol(self):
        from coops.domain.ports import StoragePort

        assert isinstance(self.make_store(), StoragePort)


class TestSaveDocumentShape:
    def test_save_writes_document_shape_and_scopes_filter(self):
        collection = FakeCollection()
        adapter = _adapter(collection)

        adapter.save(TenantId("UnB-Mds"), "bronze", "issues_2099.1-Demo", [{"n": 1}])

        assert len(collection._docs) == 1
        # The replace filter is the full 3-tuple, tenant included.
        assert collection.replace_filters[0] == {
            "tenant_id": "UnB-Mds",
            "layer": "bronze",
            "entity": "issues_2099.1-Demo",
        }
        doc = next(iter(collection._docs.values()))
        # The document shape is exactly the 4 fields of the address plus data.
        assert set(doc) == {"tenant_id", "layer", "entity", "data"}
        # The slug is stored verbatim: since #92 TenantId does no case folding.
        assert doc["tenant_id"] == "UnB-Mds"
        assert doc["layer"] == "bronze"
        assert doc["entity"] == "issues_2099.1-Demo"
        assert doc["data"] == [{"n": 1}]

    def test_tenant_is_injected_not_read_from_the_data(self):
        collection = FakeCollection()
        adapter = _adapter(collection)

        # A payload that claims its own tenant must not be able to move the
        # document into another tenant's key space.
        adapter.save(
            TenantId("org-a"), "bronze", "issues_d", {"tenant_id": "org-b"}
        )

        doc = next(iter(collection._docs.values()))
        assert doc["tenant_id"] == "org-a"
        assert doc["data"] == {"tenant_id": "org-b"}

    def test_save_twice_replaces_not_accumulates_documents(self):
        collection = FakeCollection()
        adapter = _adapter(collection)

        adapter.save(TenantId("org-a"), "bronze", "issues_d", [{"n": 1}])
        adapter.save(TenantId("org-a"), "bronze", "issues_d", [{"n": 2}])

        # One document per key, not two — count, not just the loaded value.
        assert collection.count_documents({"tenant_id": "org-a"}) == 1
        assert collection.count_documents(
            {"tenant_id": "org-a", "layer": "bronze", "entity": "issues_d"}
        ) == 1

    def test_same_entity_under_two_layers_stays_two_documents(self):
        collection = FakeCollection()
        adapter = _adapter(collection)

        adapter.save(TenantId("org-a"), "bronze", "members_detailed", [{"src": "b"}])
        adapter.save(TenantId("org-a"), "silver", "members_detailed", [{"src": "s"}])

        # The layer is part of the identity: this is the collision the
        # index would invite without its `layer` term.
        assert collection.count_documents({"tenant_id": "org-a"}) == 2


class TestReadFilters:
    def test_every_read_carries_the_tenant_term(self):
        collection = FakeCollection()
        adapter = _adapter(collection)

        adapter.save(TenantId("org-a"), "bronze", "issues_d", [])
        adapter.save(TenantId("org-b"), "bronze", "issues_e", [])
        adapter.load(TenantId("org-a"), "bronze", "issues_d")
        adapter.list(TenantId("org-a"), "bronze")

        assert collection.find_filters, "no reads were issued"
        # Every find carried a tenant_id filter; none could be issued without it.
        assert all("tenant_id" in f for f in collection.find_filters)
        assert all(f["tenant_id"] == "org-a" for f in collection.find_filters)

    def test_list_returns_entity_names_only(self):
        collection = FakeCollection()
        adapter = _adapter(collection)

        adapter.save(TenantId("org-a"), "silver", "members_detailed", [{"m": 1}])
        listed = adapter.list(TenantId("org-a"), "silver")

        assert listed == ["members_detailed"]
        # Nothing driver-shaped leaves the adapter: the read returned full
        # documents (the fake ignores the projection), and only the name came back.
        assert all(not isinstance(item, dict) for item in listed)


class TestIndex:
    def test_index_is_created_on_the_three_tuple(self):
        collection = FakeCollection()
        _adapter(collection)
        assert len(collection.indexes) == 1
        spec, kwargs = collection.indexes[0]
        assert spec == [
            ("tenant_id", 1),
            ("layer", 1),
            ("entity", 1),
        ]
        assert kwargs["unique"] is True
        assert kwargs["name"] == INDEX_NAME


class TestLifecycle:
    def test_requires_uri_or_client(self):
        with pytest.raises(ValueError, match="uri or an injected client"):
            MongoStorageAdapter()

    def test_close_closes_the_client(self):
        collection = FakeCollection()
        client = FakeClient(collection)
        adapter = MongoStorageAdapter(client=client)
        adapter.close()
        assert client.closed is True


class TestValidatedAddresses:
    """Every entry path validates, so the guards hold regardless of the caller."""

    @pytest.mark.parametrize("layer", ["raw", "Bronze", "", "bronze/", "cache", None])
    def test_save_load_and_list_reject_unknown_layers(self, layer):
        adapter = _adapter(FakeCollection())
        tenant = TenantId("org-a")
        with pytest.raises(ValueError, match="unknown layer"):
            adapter.save(tenant, layer, "issues_d", [])
        with pytest.raises(ValueError, match="unknown layer"):
            adapter.load(tenant, layer, "issues_d")
        with pytest.raises(ValueError, match="unknown layer"):
            adapter.list(tenant, layer)

    @pytest.mark.parametrize(
        "entity,match",
        [
            ("", "non-empty"),
            ("   ", "non-empty"),
            ("\t\n", "non-empty"),
            ("issues_a/b", "path separator"),
            ("issues_a\\b", "path separator"),
            ("../issues_a", "path separator"),
            ("issues_a.json", "extension"),
            ("issues_all", "_all"),
            ("commits_all", "_all"),
            (None, "non-empty"),
        ],
    )
    def test_save_and_load_reject_invalid_entities(self, entity, match):
        adapter = _adapter(FakeCollection())
        tenant = TenantId("org-a")
        with pytest.raises(ValueError, match=match):
            adapter.save(tenant, "bronze", entity, [])
        with pytest.raises(ValueError, match=match):
            adapter.load(tenant, "bronze", entity)
