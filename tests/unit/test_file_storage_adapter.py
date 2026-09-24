"""Contract tests for ``coops.storage.file.FileStorageAdapter`` (#41).

The shared ``StoragePortContract`` (in ``test_storage_port.py``) is the
behaviour every implementation of the port must hold; this module holds the
file adapter to it — the same contract ``MongoStorageAdapter`` (#39) will be
held to — and adds the cases that are specific to a filesystem mapping:

- where the data lands (``<root>/<tenant>/<layer>/<entity>.json``) and that
  the bytes match what the pipeline itself writes;
- that the files of one tenant physically live in that tenant's own subtree,
  so isolation is a property of the layout, not of a query filter;
- the adapter's own guard: a tenant slug that cannot be a directory name
  (a path separator, ``.``, ``..``) is refused rather than silently
  addressing a directory outside the tenant's subtree.

All writes go to pytest's ``tmp_path``; nothing here touches the working
directory. The real corpus is driven separately, read-only, in the PR's
validation notes.
"""

from __future__ import annotations

import json

import pytest

from coops.domain import TenantId
from coops.domain.ports import StoragePort
from coops.storage.file import FileStorageAdapter
from tests.unit.test_storage_port import StoragePortContract


class TestFileStorageAdapter(StoragePortContract):
    # The base `make_store` is shape-only; the `store` fixture below is what
    # builds the adapter, because it needs tmp_path.
    @pytest.fixture
    def store(self, tmp_path) -> StoragePort:
        return FileStorageAdapter(tmp_path / "data")

    def test_satisfies_the_port_protocol(self, store):
        assert isinstance(store, StoragePort)


class TestFileLayout:
    @pytest.fixture
    def root(self, tmp_path):
        return tmp_path / "data"

    @pytest.fixture
    def store(self, root):
        return FileStorageAdapter(root)

    def test_dataset_lands_at_tenant_layer_entity_json(self, store, root):
        store.save(TenantId("org-a"), "bronze", "issues_2099.1-Demo.App", [{"n": 1}])
        expected = root / "org-a" / "bronze" / "issues_2099.1-Demo.App.json"
        assert expected.is_file()

    def test_tenants_own_disjoint_subtrees(self, store, root):
        # The physical layout is the isolation: tenant B's file is not in
        # tenant A's directory, so no A-scoped path can reach it.
        store.save(TenantId("org-a"), "bronze", "issues_d", [{"author": "a"}])
        store.save(TenantId("org-b"), "bronze", "issues_d", [{"author": "b"}])
        assert list((root / "org-a").iterdir()) == [root / "org-a" / "bronze"]
        assert list((root / "org-b").iterdir()) == [root / "org-b" / "bronze"]
        assert not (root / "org-a" / "bronze" / "issues_d.json").samefile(
            root / "org-b" / "bronze" / "issues_d.json"
        )

    def test_bytes_match_what_the_pipeline_writes(self, store, root):
        # indent=2, ensure_ascii=False, no trailing newline — the format of
        # every file under data/ today (#30: byte-comparable output).
        data = [{"name": "Ação & Café", "n": 1}]
        store.save(TenantId("org-a"), "silver", "members_detailed", data)
        written = (root / "org-a" / "silver" / "members_detailed.json").read_text(
            encoding="utf-8"
        )
        assert written == json.dumps(data, indent=2, ensure_ascii=False)
        assert not written.endswith("\n")
        assert "Ação" in written  # not \u00e9-escaped

    def test_save_creates_the_tree_read_does_not(self, store, root):
        # A read against a root that does not exist yet answers "nothing"
        # and leaves the filesystem untouched.
        assert store.load(TenantId("never"), "bronze", "issues_d") is None
        assert store.list(TenantId("never"), "bronze") == []
        assert not root.exists()
        store.save(TenantId("later"), "gold", "executive_dashboard", {})
        assert (root / "later" / "gold").is_dir()

    def test_list_ignores_non_dataset_files_in_a_legacy_directory(self, store, root):
        # A directory inherited from an older run may hold the publish
        # aggregates and other non-dataset files; only addressable entity
        # names come back.
        store.save(TenantId("org-a"), "bronze", "issues_d", [])
        bronze = root / "org-a" / "bronze"
        (bronze / "issues_all.json").write_text("[]", encoding="utf-8")
        (bronze / "notes.txt").write_text("x", encoding="utf-8")
        (bronze / "commits_d").mkdir()
        assert store.list(TenantId("org-a"), "bronze") == ["issues_d"]


class TestTenantSlugGuard:
    """The adapter's own guard: the slug becomes a path component."""

    @pytest.fixture
    def store(self, tmp_path):
        return FileStorageAdapter(tmp_path / "data")

    @pytest.mark.parametrize("slug", ["org/a", "org\\a", "..", "."])
    def test_unusable_slugs_are_refused_and_write_nothing(self, store, tmp_path, slug):
        with pytest.raises(ValueError, match="directory name"):
            store.save(TenantId(slug), "bronze", "issues_d", [])
        # Fail closed: nothing was written anywhere under the root — and
        # the root itself was not created.
        assert not (tmp_path / "data").exists()

    def test_usable_slug_still_works(self, store, tmp_path):
        # Control for the guard above: a plain slug passes it. ("unb-mds"
        # is the deployment's actual slug.)
        store.save(TenantId("unb-mds"), "bronze", "issues_d", [])
        assert store.list(TenantId("unb-mds"), "bronze") == ["issues_d"]
