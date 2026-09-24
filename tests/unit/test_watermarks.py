"""Unit tests for coops.bronze.watermarks — the per-repository watermark store.

The store is plain JSON on disk, so these tests exercise the real file round
trip (in ``tmp_path``) plus the comparison/query helpers the extractors rely on.
"""

from datetime import datetime, timezone

from coops.bronze.watermarks import WatermarkStore, max_iso, query_since


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


class TestMaxIso:
    def test_returns_the_later_value(self):
        assert max_iso("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z") == "2026-01-02T00:00:00Z"
        assert max_iso("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z") == "2026-01-02T00:00:00Z"

    def test_none_passes_through_the_other_side(self):
        assert max_iso(None, "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
        assert max_iso("2026-01-01T00:00:00Z", None) == "2026-01-01T00:00:00Z"
        assert max_iso(None, None) is None

    def test_ties_return_the_first(self):
        assert max_iso("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"


class TestQuerySince:
    def test_subtracts_one_second(self):
        assert query_since("2026-01-01T00:00:00Z") == "2025-12-31T23:59:59Z"

    def test_offset_timestamp_is_normalised_to_utc(self):
        assert query_since("2026-01-01T03:00:00+03:00") == "2025-12-31T23:59:59Z"

    def test_malformed_or_missing_is_returned_unchanged(self):
        assert query_since("not-a-date") == "not-a-date"
        assert query_since(None) is None


class TestWatermarkStore:
    def _store(self, tmp_path, **fields):
        now = _dt("2026-09-22T10:00:00Z")
        store = WatermarkStore(str(tmp_path / "watermarks.json"), now=now)
        if fields:
            store.update("org/repo1", **fields)
        return store, now

    def test_missing_file_yields_blank_watermark(self, tmp_path):
        store = WatermarkStore(str(tmp_path / "nope.json"))
        wm = store.get("org/repo1")
        assert wm.repo == "org/repo1"
        assert wm.last_run is None
        assert wm.head_shas == {}
        assert wm.last_event_id is None

    def test_corrupt_file_is_treated_as_absent(self, tmp_path):
        path = tmp_path / "watermarks.json"
        path.write_text("{ this is not json", encoding="utf-8")
        store = WatermarkStore(str(path))
        assert store.get("org/repo1").last_run is None

    def test_round_trip_preserves_fields(self, tmp_path):
        store, now = self._store(
            tmp_path,
            last_updated_at="2026-09-21T10:00:00Z",
            last_event_id=42,
            head_shas={"main": "abc123"},
        )
        store.save()

        reloaded = WatermarkStore(str(tmp_path / "watermarks.json"), now=now)
        wm = reloaded.get("org/repo1")
        assert wm.last_updated_at == "2026-09-21T10:00:00Z"
        assert wm.last_event_id == 42
        assert wm.head_shas == {"main": "abc123"}
        assert wm.last_run == "2026-09-22T10:00:00Z"

    def test_last_run_is_stamped_at_save_not_update(self, tmp_path):
        # `last_run` marks the *previous* run; it must not be set by `update`,
        # otherwise a later extractor in the same run would read this run's
        # timestamp and wrongly treat the run as incremental (the #110 bug).
        store, now = self._store(tmp_path)
        wm = store.update("org/repo1")
        assert store.get("org/repo1").last_run is None
        assert wm.last_run is None
        store.save()
        reloaded = WatermarkStore(str(tmp_path / "watermarks.json"), now=now)
        assert reloaded.get("org/repo1").last_run == "2026-09-22T10:00:00Z"

    def test_update_ignores_none_fields(self, tmp_path):
        store, _ = self._store(tmp_path, last_event_id=5)
        store.update("org/repo1", last_event_id=None, last_updated_at=None)
        assert store.get("org/repo1").last_event_id == 5

    def test_unknown_repo_not_persisted_until_updated(self, tmp_path):
        store, _ = self._store(tmp_path)
        store.get("org/untouched")
        store.save()
        reloaded = WatermarkStore(str(tmp_path / "watermarks.json"))
        assert reloaded.get("org/untouched").last_run is None
