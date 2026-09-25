"""Unit tests for coops.etl.gold_aggregate."""

import json
import re
import sys
from unittest.mock import patch

import pytest

from coops.etl import gold_aggregate

META = {"_metadata": {"extracted_at": "2026-01-01"}}


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def write(tmp_path, rel, data):
    path = tmp_path / "data" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def run(tmp_path):
    with patch.object(sys, "argv", ["coops-aggregate"]):
        gold_aggregate.main()
    return json.loads((tmp_path / "data" / "gold" / "executive_dashboard.json").read_text())


def test_total_members_counts_members_without_profile(tmp_path):
    write(tmp_path, "bronze/members_detailed.json", [
        META,
        {"login": "a", "profile_fetched": True},
        {"login": "b", "profile_fetched": True},
        {"login": "c", "profile_fetched": False},
    ])
    write(tmp_path, "silver/members_analytics.json", [
        META,
        {"login": "a", "status": "new"},
        {"login": "b", "status": "established"},
    ])

    health = run(tmp_path)["organization_health"]

    assert health["total_members"] == 3
    assert health["members_with_profile"] == 2
    assert (health["new_members"], health["established_members"]) == (1, 1)


def test_total_members_without_bronze_file_uses_analytics(tmp_path):
    write(tmp_path, "silver/members_analytics.json", [META, {"login": "a", "status": "new"}])

    health = run(tmp_path)["organization_health"]

    assert health["total_members"] == 1
    assert health["members_with_profile"] == 1


def test_no_data_produces_zeroed_kpis(tmp_path):
    kpis = run(tmp_path)

    assert kpis["organization_health"] == {
        "total_members": 0, "members_with_profile": 0, "active_contributors": 0,
        "new_members": 0, "established_members": 0,
    }
    assert not (tmp_path / "data" / "gold" / "performance_tiers.json").exists()


def test_performance_tiers_from_contribution_metrics(tmp_path):
    write(tmp_path, "silver/contribution_metrics.json", [META] + [
        {"user": f"u{i}", "total_contributions": c, "has_contributed": c > 0}
        for i, c in enumerate([100, 50, 10, 5, 0])
    ])

    kpis = run(tmp_path)
    tiers = json.loads((tmp_path / "data" / "gold" / "performance_tiers.json").read_text())

    assert kpis["organization_health"]["active_contributors"] == 4
    assert [c["user"] for c in kpis["top_contributors"]] == ["u0", "u1", "u2", "u3", "u4"]
    assert [c["user"] for c in tiers["non_contributors"]] == ["u4"]
    # generated_at is a scalar, not a tier list.
    assert sum(len(v) for v in tiers.values() if isinstance(v, list)) == 5


def test_performance_tiers_generated_at(tmp_path):
    """performance_tiers.json carries generated_at, in the same format and
    from the same clock reading as the dashboard written by the same run —
    one timestamp, so the two artifacts cannot disagree about freshness."""
    write(tmp_path, "silver/contribution_metrics.json", [META] + [
        {"user": f"u{i}", "total_contributions": c, "has_contributed": c > 0}
        for i, c in enumerate([100, 50, 10, 5, 0])
    ])

    kpis = run(tmp_path)
    tiers = json.loads((tmp_path / "data" / "gold" / "performance_tiers.json").read_text())

    assert tiers["generated_at"] == kpis["generated_at"]
    # Same isoformat shape the dashboard uses: YYYY-MM-DDTHH:MM:SS[.ffffff].
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", tiers["generated_at"])


def test_generated_at_is_utc_with_offset(tmp_path):
    """generated_at é UTC consciente de fuso: o valor carrega o próprio
    offset (+00:00), então uma execução no Actions e uma local produzem
    instantes comparáveis (#143)."""
    write(tmp_path, "silver/contribution_metrics.json", [META] + [
        {"user": f"u{i}", "total_contributions": c, "has_contributed": c > 0}
        for i, c in enumerate([100, 50, 10, 5, 0])
    ])

    kpis = run(tmp_path)
    tiers = json.loads((tmp_path / "data" / "gold" / "performance_tiers.json").read_text())

    assert kpis["generated_at"].endswith("+00:00")
    assert tiers["generated_at"].endswith("+00:00")
