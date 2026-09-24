"""Unit tests for coops.storage.file — the filesystem StoragePort driver.

The port contract — tenant isolation above all — is inherited from
``StoragePortContract``, the same suite the in-memory reference runs (and
#39's Mongo adapter will run), so every implementation is held to the same
behaviours. What lives here is what the filesystem adds: the on-disk
layout, the byte format of the files, reads that create nothing, and the
guards that keep one tenant's tree from addressing another's.

The real corpus holds one tenant, so the multi-tenant cases are necessarily
synthetic (the org-a/org-b fixtures here and in the contract); the corpus
itself is driven read-only in the PR's validation notes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coops.domain import TenantId
from coops.domain.ports import JSONValue, StoragePort
from coops.storage.file import FileStorageAdapter
from tests.unit.test_storage_port import StoragePortContract

ORG_A = TenantId("org-a")


def _corpus_json(data: JSONValue) -> str:
    """The exact bytes the ETL's JSON writers produce (indent=2, non-ASCII kept)."""
    return json.dumps(data, indent=2, ensure_ascii=False)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "storage"


@pytest.fixture
def store(root: Path) -> FileStorageAdapter:
    return FileStorageAdapter(root)


class TestFileStorageAdapterPort(StoragePortContract):
    """The shared contract, pointed at a per-test temporary directory."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> StoragePort:
        return FileStorageAdapter(tmp_path / "storage")


class TestLayout:
    def test_save_writes_tenant_layer_entity_json(self, store, root):
        store.save(ORG_A, "bronze", "issues_2099.1-Demo.App", [{"n": 1}])
        expected = root / "org-a" / "bronze" / "issues_2099.1-Demo.App.json"
        assert expected.is_file()

    def test_file_bytes_are_the_corpus_format(self, store, root):
        data: JSONValue = [{"number": 1, "title": "Ação — проверка"}]
        store.save(ORG_A, "silver", "members_detailed", data)
        written = (root / "org-a" / "silver" / "members_detailed.json").read_text(
            encoding="utf-8"
        )
        # Whole artifact, not a prefix: the format is what #30 compares on.
        assert written == _corpus_json(data)

    def test_resave_leaves_exactly_one_file(self, store, root):
        store.save(ORG_A, "gold", "executive_dashboard", {"v": 1})
        store.save(ORG_A, "gold", "executive_dashboard", {"v": 2})
        layer = root / "org-a" / "gold"
        assert sorted(p.name for p in layer.iterdir()) == ["executive_dashboard.json"]
        assert json.loads(
            (layer / "executive_dashboard.json").read_text(encoding="utf-8")
        ) == {"v": 2}


class TestTenantTrees:
    def test_tenants_get_disjoint_trees(self, store, root):
        store.save(ORG_A, "bronze", "issues_d", [{"author": "a"}])
        store.save(TenantId("org-b"), "bronze", "issues_d", [{"author": "b"}])

        tree_a = root / "org-a" / "bronze" / "issues_d.json"
        tree_b = root / "org-b" / "bronze" / "issues_d.json"
        assert tree_a.read_text(encoding="utf-8") == _corpus_json([{"author": "a"}])
        assert tree_b.read_text(encoding="utf-8") == _corpus_json([{"author": "b"}])
        # Absence: the whole root holds exactly the two tenants' own files
        # and nothing else — no third path, no shared file.
        assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.json")) == [
            "org-a/bronze/issues_d.json",
            "org-b/bronze/issues_d.json",
        ]


class TestReadsCreateNothing:
    def test_never_written_tenant_reads_empty_and_creates_nothing(self, store, root):
        newcomer = TenantId("unb-mds")
        assert store.load(newcomer, "bronze", "issues_d") is None
        assert store.list(newcomer, "gold") == []
        assert not root.exists()

    def test_list_of_a_missing_layer_directory_is_empty(self, store, root):
        store.save(ORG_A, "bronze", "issues_d", [])
        assert store.list(ORG_A, "silver") == []
        assert not (root / "org-a" / "silver").exists()


class TestTenantSlugIsOneDirectory:
    @pytest.mark.parametrize("slug", ["org/a", "../org-b", "org\\a", "..", "."])
    def test_save_rejects_a_slug_that_is_not_one_directory(self, store, root, slug):
        # A separator or dot-name would escape the tenant's subtree — into
        # another tenant's tree or out of the root — so it must not write.
        with pytest.raises(ValueError, match="single directory"):
            store.save(TenantId(slug), "bronze", "issues_d", [])
        assert not root.exists()

    def test_every_read_path_rejects_it_too(self, store, root):
        with pytest.raises(ValueError, match="single directory"):
            store.load(TenantId("org/a"), "bronze", "issues_d")
        with pytest.raises(ValueError, match="single directory"):
            store.list(TenantId("../org-b"), "bronze")


class TestUnaddressableFilesOnDisk:
    """Pre-#170 runs left ``*_all.json`` beside real datasets on disk.

    The port promises such files stay unreadable and unlistable even though
    they exist; these tests place them by hand, which is the only way to
    get one (the port cannot write them).
    """

    @pytest.fixture
    def legacy_root(self, root: Path) -> Path:
        layer = root / "org-a" / "bronze"
        layer.mkdir(parents=True)
        (layer / "issues_all.json").write_text("[]", encoding="utf-8")
        (layer / "issues_2099.1-Demo.json").write_text('[{"n": 1}]', encoding="utf-8")
        (layer / "notes.txt").write_text("not a dataset", encoding="utf-8")
        (layer / "issues_staged.json.tmp").write_text("[", encoding="utf-8")
        return root

    def test_list_skips_aggregates_and_non_datasets(self, store, legacy_root):
        assert store.list(ORG_A, "bronze") == ["issues_2099.1-Demo"]

    def test_load_of_an_aggregate_raises_though_the_file_exists(
        self, store, legacy_root
    ):
        with pytest.raises(ValueError, match="_all"):
            store.load(ORG_A, "bronze", "issues_all")
