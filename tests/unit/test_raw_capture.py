"""Unit tests for coops.raw_capture.capture — the corpus-raw artifact."""

import json
import os
import stat

from coops.raw_capture.capture import CaptureRecord, RawCaptureWriter


def _mode(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_capture_record_shape_keys_in_index_order():
    record = CaptureRecord(
        tenant_id="unb-mds",
        provider="github",
        endpoint="/repos/unb-mds/coops",
        params={"per_page": "50"},
        etag='"abc"',
        fetched_at="2026-09-22T12:00:00+00:00",
        payload={"id": 1},
    )
    assert list(record.to_dict().keys()) == [
        "tenant_id",
        "provider",
        "endpoint",
        "params",
        "etag",
        "fetched_at",
        "payload",
    ]


def test_capture_record_round_trip():
    record = CaptureRecord(
        tenant_id="unb-mds",
        provider="github",
        endpoint="/graphql",
        params={"query": "{ viewer { login } }", "variables": {}},
        etag=None,
        fetched_at="2026-09-22T12:00:00+00:00",
        payload=[{"login": "octocat"}],
    )
    restored = CaptureRecord.from_dict(json.loads(json.dumps(record.to_dict())))
    assert restored == record
    assert restored.etag is None


def test_from_dict_defaults_provider():
    record = CaptureRecord.from_dict(
        {
            "tenant_id": "unb-mds",
            "endpoint": "/repos/x",
            "params": {},
            "etag": None,
            "fetched_at": "2026-09-22T12:00:00+00:00",
            "payload": {},
        }
    )
    assert record.provider == "github"


def test_writer_writes_tenant_scoped_record(tmp_path):
    root = str(tmp_path / "corpus-raw")
    writer = RawCaptureWriter(root, "unb-mds")

    writer.write("/repos/unb-mds/coops", {"per_page": "50"}, '"etag"', {"id": 1})

    tenant_dir = os.path.join(root, "unb-mds")
    files = os.listdir(tenant_dir)
    assert len(files) == 1
    assert files[0].endswith(".json")

    with open(os.path.join(tenant_dir, files[0]), encoding="utf-8") as f:
        saved = json.load(f)

    assert saved["tenant_id"] == "unb-mds"
    assert saved["provider"] == "github"
    assert saved["endpoint"] == "/repos/unb-mds/coops"
    assert saved["params"] == {"per_page": "50"}
    assert saved["etag"] == '"etag"'
    assert saved["payload"] == {"id": 1}
    assert "fetched_at" in saved


def test_writer_directories_700_files_600(tmp_path):
    root = str(tmp_path / "corpus-raw")
    writer = RawCaptureWriter(root, "unb-mds")
    writer.write("/repos/x", {}, None, {"id": 1})

    tenant_dir = os.path.join(root, "unb-mds")
    assert _mode(root) == 0o700
    assert _mode(tenant_dir) == 0o700
    for name in os.listdir(tenant_dir):
        assert _mode(os.path.join(tenant_dir, name)) == 0o600


def test_writer_overwrites_same_request(tmp_path):
    root = str(tmp_path / "corpus-raw")
    writer = RawCaptureWriter(root, "unb-mds")
    writer.write("/repos/x", {"page": "1"}, '"a"', {"v": 1})
    writer.write("/repos/x", {"page": "1"}, '"b"', {"v": 2})

    tenant_dir = os.path.join(root, "unb-mds")
    files = [f for f in os.listdir(tenant_dir) if f.endswith(".json")]
    assert len(files) == 1
    with open(os.path.join(tenant_dir, files[0]), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["payload"] == {"v": 2}
    assert saved["etag"] == '"b"'


def test_writer_distinguishes_by_params(tmp_path):
    root = str(tmp_path / "corpus-raw")
    writer = RawCaptureWriter(root, "unb-mds")
    writer.write("/repos/x", {"page": "1"}, None, {"page": 1})
    writer.write("/repos/x", {"page": "2"}, None, {"page": 2})

    tenant_dir = os.path.join(root, "unb-mds")
    files = [f for f in os.listdir(tenant_dir) if f.endswith(".json")]
    assert len(files) == 2


def test_prune_removes_only_stale_records(tmp_path):
    root = str(tmp_path / "corpus-raw")
    writer = RawCaptureWriter(root, "unb-mds")
    writer.write("/repos/stale", {}, None, {}, fetched_at="2020-01-01T00:00:00+00:00")
    writer.write("/repos/fresh", {}, None, {}, fetched_at="2999-01-01T00:00:00+00:00")

    removed = writer.prune(max_age_days=30)

    assert removed == 1
    remaining = writer.record_files()
    assert len(remaining) == 1
    with open(os.path.join(writer.tenant_dir, remaining[0]), encoding="utf-8") as f:
        assert json.load(f)["endpoint"] == "/repos/fresh"


def test_prune_tolerates_unparseable_fetched_at(tmp_path):
    root = str(tmp_path / "corpus-raw")
    writer = RawCaptureWriter(root, "unb-mds")
    writer.write("/repos/bad", {}, None, {}, fetched_at="not-a-date")

    # An unparseable timestamp is neither provably stale nor fresh: keep it.
    assert writer.prune(max_age_days=30) == 0
    assert len(writer.record_files()) == 1


def test_record_files_empty_when_no_tenant_dir(tmp_path):
    writer = RawCaptureWriter(str(tmp_path / "corpus-raw"), "unb-mds")
    assert writer.record_files() == []
    assert list(writer.iter_records()) == []


def test_parse_fetched_at_handles_missing_and_z():
    from coops.raw_capture.capture import _parse_fetched_at

    assert _parse_fetched_at("") is None
    assert _parse_fetched_at("not-a-date") is None
    assert _parse_fetched_at("2026-09-22T12:00:00Z") is not None
    assert _parse_fetched_at("2026-09-22T12:00:00+00:00") is not None


def test_prune_directory_walks_tenants(tmp_path):
    from coops.raw_capture.capture import prune_directory

    root = str(tmp_path / "corpus-raw")
    stale = RawCaptureWriter(root, "tenant-a")
    stale.write("/repos/old", {}, None, {}, fetched_at="2020-01-01T00:00:00+00:00")
    fresh = RawCaptureWriter(root, "tenant-b")
    fresh.write("/repos/new", {}, None, {}, fetched_at="2999-01-01T00:00:00+00:00")

    assert prune_directory(root, 30) == 1
    assert stale.record_files() == []
    assert len(fresh.record_files()) == 1


def test_prune_directory_missing_root_is_noop(tmp_path):
    from coops.raw_capture.capture import prune_directory

    assert prune_directory(str(tmp_path / "nope"), 30) == 0
