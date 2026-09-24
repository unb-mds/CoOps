"""Contract tests for coops.domain.ports.storage_port — the storage port.

A `Protocol` ships no behaviour of its own, so three things are tested here:

1. **The interface shape** — every method takes the `TenantId` first and
   positionally. That structural fact *is* the tenant guard: it is what
   leaves "no method that addresses datasets without one" true.
2. **The address validators and the `StoredDataset` value object** — the
   guards that make an invalid or double-counting address unrepresentable.
3. **The contract** every implementation must hold, run against a reference
   in-memory implementation (`InMemoryStorage`). #39's `MongoStorageAdapter`
   will be held to the same contract by subclassing `StoragePortContract`.

Entity names in these tests are synthetic (semester 2099.1 does not exist).
They mirror the shapes on disk — per-repository with dots and underscores,
organisation-wide, per-layer — because a port that cannot express those
shapes is wrong; the real corpus is driven separately, read-only, in the
PR's validation notes.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from typing import get_type_hints

import pytest

from coops.domain import TenantId
from coops.domain.ports import (
    LAYERS,
    JSONValue,
    StoragePort,
    StoredDataset,
    validate_entity,
    validate_layer,
)

PORT_METHODS = ("save", "load", "list")


class InMemoryStorage:
    """Reference `StoragePort`: one dict keyed by ``(tenant_id, layer, entity)``.

    The tenant is part of the storage key — injected here, never read from
    the data — which is where the isolation the port promises is actually
    enforced. The address validators run on every entry path, so an invalid
    address raises before anything is stored.
    """

    def __init__(self) -> None:
        self._datasets: dict[tuple[str, str, str], JSONValue] = {}

    @staticmethod
    def _key(tenant: TenantId, layer: str, entity: str) -> tuple[str, str, str]:
        return (str(tenant), validate_layer(layer), validate_entity(entity))

    def save(
        self,
        tenant: TenantId,
        layer: str,
        entity: str,
        data: JSONValue,
    ) -> None:
        self._datasets[self._key(tenant, layer, entity)] = data

    def load(
        self,
        tenant: TenantId,
        layer: str,
        entity: str,
    ) -> StoredDataset | None:
        key = self._key(tenant, layer, entity)
        if key not in self._datasets:
            return None
        return StoredDataset(
            tenant=tenant, layer=layer, entity=entity, data=self._datasets[key]
        )

    def list(self, tenant: TenantId, layer: str) -> list[str]:
        layer = validate_layer(layer)
        tenant_id = str(tenant)
        return sorted(
            entity
            for (stored_tenant, stored_layer, entity) in self._datasets
            if stored_tenant == tenant_id and stored_layer == layer
        )


class TestPortShape:
    """The tenant guard is the signature: assert it, so it cannot be edited away."""

    @staticmethod
    def _parameters(method_name: str) -> dict[str, inspect.Parameter]:
        parameters = inspect.signature(getattr(StoragePort, method_name)).parameters
        return {name: p for name, p in parameters.items() if name != "self"}

    @pytest.mark.parametrize("method_name", PORT_METHODS)
    def test_tenant_is_the_first_parameter(self, method_name):
        assert next(iter(self._parameters(method_name))) == "tenant"

    @pytest.mark.parametrize("method_name", PORT_METHODS)
    def test_tenant_is_required_not_defaulted(self, method_name):
        parameters = self._parameters(method_name)
        assert parameters["tenant"].default is inspect.Parameter.empty

    @pytest.mark.parametrize("method_name", PORT_METHODS)
    def test_tenant_parameter_is_the_domain_type(self, method_name):
        hints = get_type_hints(getattr(StoragePort, method_name))
        assert hints["tenant"] is TenantId

    def test_reference_fake_satisfies_the_protocol(self):
        assert isinstance(InMemoryStorage(), StoragePort)


class TestValidateLayer:
    def test_accepts_the_three_medallion_layers(self):
        assert sorted(LAYERS) == ["bronze", "gold", "silver"]
        for layer in ("bronze", "silver", "gold"):
            assert validate_layer(layer) == layer

    @pytest.mark.parametrize("layer", ["raw", "Bronze", "", "bronze/", "cache", None])
    def test_rejects_anything_else(self, layer):
        with pytest.raises(ValueError):
            validate_layer(layer)

    def test_raw_is_rejected_because_it_has_its_own_port(self):
        # RawStore keys capture by (provider, endpoint, params_hash); letting
        # "raw" in here would address a second key space through this one.
        with pytest.raises(ValueError, match="raw"):
            validate_layer("raw")


class TestValidateEntity:
    @pytest.mark.parametrize(
        "entity",
        [
            # Per-repository shapes, as found under data/bronze and
            # data/silver — dots and underscores inside repository names.
            "commits_2099.1-Demo.App",
            "issues_2099.1-Demo",
            "prs_2099.1-Demo_api",
            "issue_events_2099.1-Demo",
            "repo_2099.1-Demo",
            "structure_2099.1-Demo",
            "hierarchy_2099.1-Demo",
            "language_analysis_2099.1-Demo",
            # Organisation-wide datasets.
            "members_detailed",
            "members_statistics",
            "activity_heatmap",
            # Gold.
            "executive_dashboard",
            "timeline_last_7_days",
            # Bookkeeping that lives inside a layer directory.
            "registry",
        ],
    )
    def test_accepts_every_dataset_shape_on_disk_today(self, entity):
        assert validate_entity(entity) == entity

    def test_trims_surrounding_whitespace(self):
        assert validate_entity("  issues_2099.1-Demo \t") == "issues_2099.1-Demo"

    @pytest.mark.parametrize("entity", ["", "   ", "\t\n"])
    def test_rejects_blank_names(self, entity):
        with pytest.raises(ValueError, match="non-empty"):
            validate_entity(entity)

    @pytest.mark.parametrize("entity", ["issues_a/b", "issues_a\\b", "../issues_a"])
    def test_rejects_path_separators(self, entity):
        with pytest.raises(ValueError, match="path separator"):
            validate_entity(entity)

    @pytest.mark.parametrize("entity", ["issues_a.json"])
    def test_rejects_the_json_extension(self, entity):
        # Exactly the lowercase extension the file adapter appends: the
        # aliasing risk is one dataset addressable as both ``issues_a`` and
        # ``issues_a.json``. Uppercase ``.JSON`` is a different — if odd —
        # name, not an alias, and stays allowed.
        with pytest.raises(ValueError, match="extension"):
            validate_entity(entity)

    @pytest.mark.parametrize(
        "entity",
        [
            "issues_all",
            "commits_all",
            "prs_all",
            "issue_events_all",
            "members_all",
        ],
    )
    def test_rejects_the_publish_aggregate_suffix(self, entity):
        # The *_all files repeat every per-repository record of their kind:
        # reading one alongside the per-repository entities double-counts.
        with pytest.raises(ValueError, match="_all"):
            validate_entity(entity)


class TestStoredDataset:
    def _dataset(self, **overrides) -> StoredDataset:
        kwargs: dict = {
            "tenant": TenantId("org-a"),
            "layer": "bronze",
            "entity": "issues_2099.1-Demo",
            "data": [{"number": 1}],
        }
        kwargs.update(overrides)
        return StoredDataset(**kwargs)

    def test_is_frozen(self):
        with pytest.raises(FrozenInstanceError):
            self._dataset().__setattr__("entity", "issues_other")

    def test_carries_the_domain_tenant_not_a_string(self):
        dataset = self._dataset()
        assert isinstance(dataset.tenant, TenantId)
        assert dataset.tenant == TenantId("org-a")

    def test_rejects_an_unknown_layer_at_construction(self):
        with pytest.raises(ValueError, match="unknown layer"):
            self._dataset(layer="raw")

    def test_rejects_the_publish_aggregate_at_construction(self):
        with pytest.raises(ValueError, match="_all"):
            self._dataset(entity="issues_all")

    def test_normalises_the_entity_on_construction(self):
        assert self._dataset(entity=" issues_2099.1-Demo ").entity == (
            "issues_2099.1-Demo"
        )


class StoragePortContract:
    """Behaviours every `StoragePort` implementation must hold.

    Subclass and implement `make_store`. The tenant-isolation tests are the
    point of the port: a store holding two tenants' datasets must never
    return tenant B's data for a tenant A query, on any method.
    """

    def make_store(self) -> StoragePort:
        raise NotImplementedError

    @pytest.fixture
    def store(self) -> StoragePort:
        return self.make_store()

    def test_save_then_load_round_trips_the_data(self, store):
        data: JSONValue = [{"number": 1}, {"number": 2}]
        store.save(TenantId("org-a"), "bronze", "issues_2099.1-Demo", data)
        loaded = store.load(TenantId("org-a"), "bronze", "issues_2099.1-Demo")
        assert loaded is not None
        assert loaded.data == data
        assert loaded.tenant == TenantId("org-a")
        assert loaded.layer == "bronze"
        assert loaded.entity == "issues_2099.1-Demo"

    def test_load_of_a_missing_address_returns_none(self, store):
        assert store.load(TenantId("org-a"), "bronze", "issues_none") is None

    def test_second_save_replaces_not_accumulates(self, store):
        # Both saves present at once; the second must win outright. A merge
        # here is how a dataset comes to hold two copies of the same records.
        store.save(TenantId("org-a"), "bronze", "issues_d", [{"n": 1}])
        store.save(TenantId("org-a"), "bronze", "issues_d", [{"n": 2}])
        loaded = store.load(TenantId("org-a"), "bronze", "issues_d")
        assert loaded is not None
        assert loaded.data == [{"n": 2}]

    def test_tenant_isolation_same_address_different_tenants(self, store):
        tenant_a = TenantId("org-a")
        tenant_b = TenantId("org-b")
        store.save(tenant_a, "bronze", "issues_d", [{"author": "a"}])
        store.save(tenant_b, "bronze", "issues_d", [{"author": "b"}])

        loaded_a = store.load(tenant_a, "bronze", "issues_d")
        loaded_b = store.load(tenant_b, "bronze", "issues_d")
        assert loaded_a is not None and loaded_a.data == [{"author": "a"}]
        assert loaded_b is not None and loaded_b.data == [{"author": "b"}]

        # A tenant with nothing stored at that address reads nothing —
        # not the other tenant's data.
        assert store.load(TenantId("org-c"), "bronze", "issues_d") is None

    def test_list_is_scoped_to_the_tenant(self, store):
        store.save(TenantId("org-a"), "bronze", "issues_alpha", [])
        store.save(TenantId("org-a"), "bronze", "issues_beta", [])
        store.save(TenantId("org-b"), "bronze", "issues_gamma", [])

        listed = store.list(TenantId("org-a"), "bronze")
        assert listed == ["issues_alpha", "issues_beta"]
        assert "issues_gamma" not in listed

    def test_list_is_scoped_to_the_layer(self, store):
        store.save(TenantId("org-a"), "bronze", "issues_d", [])
        store.save(TenantId("org-a"), "silver", "issues_d", [])
        assert store.list(TenantId("org-a"), "bronze") == ["issues_d"]
        assert store.list(TenantId("org-a"), "silver") == ["issues_d"]

    def test_layer_distinguishes_the_same_entity_name(self, store):
        # Same entity under two layers, both present: each load answers
        # from its own layer.
        store.save(TenantId("org-a"), "bronze", "issues_d", [{"src": "bronze"}])
        store.save(TenantId("org-a"), "silver", "issues_d", [{"src": "silver"}])
        assert store.load(TenantId("org-a"), "bronze", "issues_d").data == [
            {"src": "bronze"}
        ]
        assert store.load(TenantId("org-a"), "silver", "issues_d").data == [
            {"src": "silver"}
        ]

    def test_list_of_an_empty_layer_is_empty(self, store):
        assert store.list(TenantId("org-a"), "gold") == []

    def test_list_returns_names_sorted(self, store):
        for name in ("issues_zeta", "issues_alpha", "issues_mu"):
            store.save(TenantId("org-a"), "bronze", name, [])
        assert store.list(TenantId("org-a"), "bronze") == [
            "issues_alpha",
            "issues_mu",
            "issues_zeta",
        ]

    def test_tenant_id_spellings_stay_separate_tenants(self, store):
        # Since #92 the slug is an assigned identifier compared exactly; the
        # GitHub case-insensitivity rule lives in ProviderAccount. Different
        # spellings address different tenants: no collapse, no overwrite.
        store.save(TenantId("ORG-A"), "bronze", "issues_d", [{"n": 1}])
        store.save(TenantId("org-a"), "bronze", "issues_d", [{"n": 2}])

        upper = store.load(TenantId("ORG-A"), "bronze", "issues_d")
        lower = store.load(TenantId("org-a"), "bronze", "issues_d")
        assert upper is not None and upper.data == [{"n": 1}]
        assert lower is not None and lower.data == [{"n": 2}]
        assert store.list(TenantId("Org-A"), "bronze") == []

    def test_entity_name_spellings_collapse_to_one_address(self, store):
        store.save(TenantId("org-a"), "bronze", " issues_d ", [{"n": 1}])
        store.save(TenantId("org-a"), "bronze", "issues_d\t", [{"n": 2}])

        loaded = store.load(TenantId("org-a"), "bronze", "issues_d")
        assert loaded is not None
        assert loaded.entity == "issues_d"
        assert loaded.data == [{"n": 2}]
        assert store.list(TenantId("org-a"), "bronze") == ["issues_d"]

    def test_save_rejects_the_publish_aggregate_and_stores_nothing(self, store):
        # Fail closed: not just the raise — the dataset must not be there
        # afterwards, by any read path.
        store.save(TenantId("org-a"), "bronze", "issues_d", [{"n": 1}])
        with pytest.raises(ValueError, match="_all"):
            store.save(TenantId("org-a"), "bronze", "issues_all", [{"n": 2}])
        assert store.list(TenantId("org-a"), "bronze") == ["issues_d"]
        with pytest.raises(ValueError, match="_all"):
            store.load(TenantId("org-a"), "bronze", "issues_all")

    def test_save_rejects_an_unknown_layer_and_stores_nothing(self, store):
        with pytest.raises(ValueError, match="unknown layer"):
            store.save(TenantId("org-a"), "raw", "issues_d", [{"n": 1}])
        assert store.list(TenantId("org-a"), "bronze") == []
        with pytest.raises(ValueError, match="unknown layer"):
            store.load(TenantId("org-a"), "raw", "issues_d")
        with pytest.raises(ValueError, match="unknown layer"):
            store.list(TenantId("org-a"), "raw")

    def test_document_shaped_datasets_round_trip(self, store):
        # Not every dataset on disk is a list of records: repo metadata,
        # structures and dashboards are single objects.
        data: JSONValue = {"id": 42, "name": "Demo", "tree": {"sha": "abc"}}
        store.save(TenantId("org-a"), "bronze", "repo_2099.1-Demo", data)
        loaded = store.load(TenantId("org-a"), "bronze", "repo_2099.1-Demo")
        assert loaded is not None
        assert loaded.data == data


class TestInMemoryStoragePort(StoragePortContract):
    def make_store(self) -> StoragePort:
        return InMemoryStorage()
