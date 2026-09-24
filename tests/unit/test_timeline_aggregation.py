import coops.gold.timeline_aggregation as timeline
from datetime import datetime, timedelta

def test_process_timeline_aggregation(monkeypatch):
    # daily_activity_summary com 10 dias
    base = datetime(2024, 1, 10)
    daily = []
    for i in range(10):
        d = base - timedelta(days=i)
        daily.append({
            "date": d.date().isoformat(),
            "total_events": 5 + i,
            "issues_created": 1,
            "issues_closed": 0,
            "prs_created": 1,
            "prs_closed": 0,
            "commits": 2,
            "comments": 1,
            "unique_users": 2,
            "unique_repos": 1,
            "authors": [{"name": "alice", "commits": 2, "issues_created": 1, "issues_closed": 0,
                         "prs_created": 1, "prs_closed": 0, "comments": 1}]
        })

    events = [
        {"user": "alice", "repo": "repoA"},
        {"user": "alice", "repo": "repoB"},
        {"user": "bob", "repo": "repoA"},
    ]

    def fake_load(path: str):
        if path.endswith("daily_activity_summary.json"):
            return daily
        if path.endswith("temporal_events.json"):
            return events
        return []

    saved = {}
    def fake_save(data, path, timestamp=True):
        saved[path] = data
        return path

    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", fake_save)

    files = timeline.process_timeline_aggregation()
    assert any(f.endswith("timeline_last_7_days.json") for f in files)
    last7 = saved["data/gold/timeline_last_7_days.json"]
    # Deve ter 7 entradas
    assert len(last7) == 7
    # Autora 'alice' deve ter lista de repos agregada
    assert "repositories" in last7[0]["authors"][0]
    assert set(last7[0]["authors"][0]["repositories"]) == {"repoA", "repoB"}


def test_timeline_empty_daily_summary(monkeypatch):
    """Empty daily summary returns no files."""
    def fake_load(path):
        return []

    saved = {}
    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

    files = timeline.process_timeline_aggregation()
    assert files == []


def test_timeline_metadata_stripped(monkeypatch):
    """Metadata records are stripped from both data sources."""
    base = datetime(2024, 6, 5)
    daily = [
        {"_metadata": {"ts": "2024-01-01"}},
        {"date": base.date().isoformat(), "total_events": 3,
         "issues_created": 1, "issues_closed": 0, "prs_created": 0,
         "prs_closed": 0, "commits": 2, "comments": 0,
         "unique_users": 1, "unique_repos": 1,
         "authors": [{"name": "alice", "commits": 2, "issues_created": 1,
                       "issues_closed": 0, "prs_created": 0, "prs_closed": 0, "comments": 0}]},
    ]
    events = [
        {"_metadata": {"ts": "2024-01-01"}},
        {"user": "alice", "repo": "repoA"},
    ]

    def fake_load(path):
        if "daily_activity_summary" in path:
            return daily
        if "temporal_events" in path:
            return events
        return []

    saved = {}
    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

    files = timeline.process_timeline_aggregation()
    assert len(files) == 2
    last7 = saved["data/gold/timeline_last_7_days.json"]
    assert len(last7) == 1


def test_timeline_no_valid_dates(monkeypatch):
    """Daily entries without 'date' field → no valid dates → empty result."""
    def fake_load(path):
        if "daily_activity_summary" in path:
            return [{"total_events": 1}]  # no 'date'
        return []

    saved = {}
    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

    files = timeline.process_timeline_aggregation()
    assert files == []


def test_timeline_12_month_aggregation(monkeypatch):
    """Monthly aggregation groups daily data by month."""
    # Create 60 days spanning 3 calendar months (Jan–Mar 2024)
    daily = []
    for i in range(60):
        d = datetime(2024, 3, 1) - timedelta(days=i)
        daily.append({
            "date": d.date().isoformat(),
            "total_events": 2, "issues_created": 1, "issues_closed": 0,
            "prs_created": 0, "prs_closed": 0, "commits": 1, "comments": 0,
            "unique_users": 1, "unique_repos": 1,
            "authors": [{"name": "alice", "commits": 1, "issues_created": 1,
                          "issues_closed": 0, "prs_created": 0, "prs_closed": 0, "comments": 0}],
        })

    def fake_load(path):
        if "daily_activity_summary" in path:
            return daily
        return []

    saved = {}
    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

    files = timeline.process_timeline_aggregation()
    assert any("timeline_last_12_months" in f for f in files)
    months = saved["data/gold/timeline_last_12_months.json"]
    assert len(months) >= 2
    # Each month has aggregated totals
    for m in months:
        assert m["total_events"] > 0
        assert isinstance(m["authors"], list)


def test_timeline_author_without_repos(monkeypatch):
    """Author with no entries in temporal_events gets empty repos list."""
    daily = [{
        "date": "2024-06-01",
        "total_events": 1, "issues_created": 0, "issues_closed": 0,
        "prs_created": 0, "prs_closed": 0, "commits": 1, "comments": 0,
        "unique_users": 1, "unique_repos": 1,
        "authors": [{"name": "orphan", "commits": 1, "issues_created": 0,
                      "issues_closed": 0, "prs_created": 0, "prs_closed": 0, "comments": 0}],
    }]

    def fake_load(path):
        if "daily_activity_summary" in path:
            return daily
        return []

    saved = {}
    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", lambda d, p, **kw: (saved.update({p: d}), p)[1])

    files = timeline.process_timeline_aggregation()
    last7 = saved["data/gold/timeline_last_7_days.json"]
    assert last7[0]["authors"][0]["repositories"] == []


def test_timeline_carries_author_id(monkeypatch):
    """The author `id` from Silver flows through both Gold aggregations
    untouched, alongside the existing name-based output."""
    daily = [{
        "date": "2024-06-05",
        "total_events": 1, "issues_created": 0, "issues_closed": 0,
        "prs_created": 0, "prs_closed": 0, "commits": 1, "comments": 0,
        "unique_users": 1, "unique_repos": 1,
        "authors": [{
            "id": "user-42",
            "name": "alice",
            "commits": 1, "issues_created": 0, "issues_closed": 0,
            "prs_created": 0, "prs_closed": 0, "comments": 0,
        }],
    }]
    events = [{"user": "alice", "repo": "repoA"}]

    def fake_load(path):
        if path.endswith("daily_activity_summary.json"):
            return daily
        if path.endswith("temporal_events.json"):
            return events
        return []

    saved = {}

    def fake_save(data, path, timestamp=True):
        saved[path] = data
        return path

    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", fake_save)

    timeline.process_timeline_aggregation()

    last7 = saved["data/gold/timeline_last_7_days.json"]
    assert last7[0]["authors"][0]["id"] == "user-42"
    assert last7[0]["authors"][0]["name"] == "alice"
    assert last7[0]["authors"][0]["repositories"] == ["repoA"]

    months = saved["data/gold/timeline_last_12_months.json"]
    assert months[0]["authors"][0]["id"] == "user-42"
    assert months[0]["authors"][0]["name"] == "alice"


def test_timeline_monthly_same_name_distinct_ids_stay_split(monkeypatch):
    """Two authors sharing a display name but holding different ids
    aggregate into TWO entries, not one merged entry (issue #151)."""
    daily = [{
        "date": "2024-06-05",
        "total_events": 2, "issues_created": 0, "issues_closed": 0,
        "prs_created": 0, "prs_closed": 0, "commits": 2, "comments": 0,
        "unique_users": 2, "unique_repos": 1,
        "authors": [
            {"id": "hash-1", "name": "Lucas Gomes", "commits": 3,
             "issues_created": 0, "issues_closed": 0, "prs_created": 0,
             "prs_closed": 0, "comments": 0},
            {"id": "hash-2", "name": "Lucas Gomes", "commits": 5,
             "issues_created": 0, "issues_closed": 0, "prs_created": 0,
             "prs_closed": 0, "comments": 0},
        ],
    }]

    def fake_load(path):
        if path.endswith("daily_activity_summary.json"):
            return daily
        return []

    saved = {}

    def fake_save(data, path, timestamp=True):
        saved[path] = data
        return path

    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", fake_save)

    timeline.process_timeline_aggregation()

    months = saved["data/gold/timeline_last_12_months.json"]
    authors = months[0]["authors"]
    assert len(authors) == 2, (
        f"same-name authors must stay split, got {len(authors)} entry"
    )
    assert {a["id"] for a in authors} == {"hash-1", "hash-2"}
    by_id = {a["id"]: a for a in authors}
    assert by_id["hash-1"]["commits"] == 3
    assert by_id["hash-2"]["commits"] == 5
    assert all(a["name"] == "Lucas Gomes" for a in authors)


def test_timeline_monthly_legacy_authors_aggregate_by_name(monkeypatch):
    """Transitional behaviour for records predating the `id` field: for
    those, `name` WAS the identity value, so two author rows with the same
    name and no `id` aggregate into ONE entry with their counts summed.
    This reproduces exactly what those pre-`id` records already meant.
    Once every record carries an `id`, this path stops being reachable
    and the test can be retired."""
    daily = [{
        "date": "2024-06-05",
        "total_events": 2, "issues_created": 0, "issues_closed": 0,
        "prs_created": 0, "prs_closed": 0, "commits": 7, "comments": 0,
        "unique_users": 2, "unique_repos": 1,
        "authors": [
            {"name": "CI/CD Bot", "commits": 2,
             "issues_created": 0, "issues_closed": 0, "prs_created": 0,
             "prs_closed": 0, "comments": 0},
            {"name": "CI/CD Bot", "commits": 5,
             "issues_created": 0, "issues_closed": 0, "prs_created": 0,
             "prs_closed": 0, "comments": 0},
        ],
    }]

    def fake_load(path):
        if path.endswith("daily_activity_summary.json"):
            return daily
        return []

    saved = {}

    def fake_save(data, path, timestamp=True):
        saved[path] = data
        return path

    monkeypatch.setattr(timeline, "load_json_data", fake_load)
    monkeypatch.setattr(timeline, "save_json_data", fake_save)

    timeline.process_timeline_aggregation()

    months = saved["data/gold/timeline_last_12_months.json"]
    authors = months[0]["authors"]
    assert len(authors) == 1, (
        f"legacy same-name authors must merge, got {len(authors)} entries"
    )
    assert authors[0]["commits"] == 7, (
        f"merged entry must sum commits (2 + 5), got {authors[0]['commits']}"
    )
    assert authors[0]["name"] == "CI/CD Bot"
