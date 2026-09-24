"""Tests for coops/silver/members_statistics.py — process_members_statistics."""

import coops.silver.members_statistics as ms


def _make_helpers(monkeypatch, *, commits=None, issues=None, prs=None, events=None):
    """Wire up fake load/save for the module under test."""
    commits = commits or []
    issues = issues or []
    prs = prs or []
    events = events or []

    def fake_load(family):
        if family == "commits":
            return commits
        if family == "issues":
            return issues
        if family == "prs":
            return prs
        if family == "issue_events":
            return events
        return []

    saved = {}

    def fake_save(data, path, timestamp=True):
        saved[path] = data
        return path

    monkeypatch.setattr(ms, "load_family", fake_load)
    monkeypatch.setattr(ms, "save_json_data", fake_save)
    return saved


# ---------------------------------------------------------------------------
# Empty / trivial data
# ---------------------------------------------------------------------------

class TestEmptyData:
    def test_all_empty(self, monkeypatch):
        saved = _make_helpers(monkeypatch)
        files = ms.process_members_statistics()
        assert any("members_statistics" in f for f in files)
        assert saved["data/silver/members_statistics.json"] == []

    def test_commits_only_no_date(self, monkeypatch):
        """Commits without a parseable date should be skipped."""
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"name": "alice"}}, "author": {"login": "alice"}}
        ])
        ms.process_members_statistics()
        assert saved["data/silver/members_statistics.json"] == []


# ---------------------------------------------------------------------------
# Commit processing
# ---------------------------------------------------------------------------

class TestCommitProcessing:
    def test_basic_commit(self, monkeypatch):
        saved = _make_helpers(monkeypatch, commits=[
            {
                "commit": {"author": {"date": "2024-01-01T10:00:00Z", "name": "Alice"}},
                "author": {"login": "alice"},
                "repo_name": "repo1",
            },
            {
                "commit": {"author": {"date": "2024-01-15T10:00:00Z", "name": "Alice"}},
                "author": {"login": "alice"},
                "repo_name": "repo1",
            },
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["name"] == "alice"
        assert stats[0]["total_commits"] == 2
        assert stats[0]["repos"] == ["repo1"]

    def test_commit_author_login_fallback(self, monkeypatch):
        """When commit.author has no login, fall back to top-level author.login."""
        saved = _make_helpers(monkeypatch, commits=[
            {
                "commit": {"author": {"date": "2024-03-01T00:00:00Z"}},
                "author": {"login": "bob"},
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["name"] == "bob"

    def test_commit_name_fallback(self, monkeypatch):
        """Fall back to commit.author.name when no login available."""
        saved = _make_helpers(monkeypatch, commits=[
            {
                "commit": {"author": {"date": "2024-03-01T00:00:00Z", "name": "Charlie"}},
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["name"] == "Charlie"


# ---------------------------------------------------------------------------
# Bot filtering
# ---------------------------------------------------------------------------

class TestBotFiltering:
    def test_bot_user_filtered_from_commits(self, monkeypatch):
        saved = _make_helpers(monkeypatch, commits=[
            {
                "commit": {"author": {"date": "2024-01-01T00:00:00Z"}},
                "author": {"login": "dependabot[bot]"},
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        assert saved["data/silver/members_statistics.json"] == []

    def test_bot_user_filtered_from_issues(self, monkeypatch):
        saved = _make_helpers(monkeypatch, issues=[
            {
                "user": {"login": "renovate[bot]"},
                "created_at": "2024-01-01T00:00:00Z",
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        assert saved["data/silver/members_statistics.json"] == []

    def test_bot_user_filtered_from_prs(self, monkeypatch):
        saved = _make_helpers(monkeypatch, prs=[
            {
                "user": {"login": "github-actions[bot]"},
                "created_at": "2024-01-01T00:00:00Z",
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        assert saved["data/silver/members_statistics.json"] == []

    def test_bot_user_filtered_from_events(self, monkeypatch):
        saved = _make_helpers(monkeypatch, events=[
            {
                "actor": {"login": "codecov[bot]"},
                "created_at": "2024-01-01T00:00:00Z",
                "event": "commented",
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        assert saved["data/silver/members_statistics.json"] == []


# ---------------------------------------------------------------------------
# Unattributed records (issues #154, #155)
# ---------------------------------------------------------------------------

class TestUnattributedRecords:
    """A record no identity channel attributes to anyone is carried in an
    explicit unattributed bucket — never a member named 'unknown'
    (temporal_analysis's old phantom) and never a silent drop
    (members_statistics' old `'unknown'` exclusion, which lost 3.7% of one
    corpus's commits)."""

    def test_commit_with_every_channel_null_is_carried_not_dropped(self, monkeypatch):
        """The #154 shape: login, name, email and author_email_hash all
        null — one marked bucket row, no member row, nothing lost."""
        saved = _make_helpers(monkeypatch, commits=[
            {
                "commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                      "login": None, "name": None,
                                      "author_email_hash": None}},
                "repo_name": "r1",
            },
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        bucket = stats[0]
        assert bucket["unattributed"] is True
        assert bucket["id"] is None
        assert bucket["name"] is None
        assert bucket["total_commits"] == 1
        assert bucket["total_events"] == 1

    def test_commit_with_author_object_entirely_absent_is_carried(self, monkeypatch):
        """The scrubbed-corpus shape (#154): no author object at all —
        the measured 2,076 records carry every channel null."""
        saved = _make_helpers(monkeypatch, commits=[
            {
                "commit": {"author": {"date": "2024-01-01T00:00:00Z"}},
                "repo_name": "r1",
            },
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["unattributed"] is True
        assert stats[0]["total_commits"] == 1

    def test_null_actor_event_is_carried_not_dropped(self, monkeypatch):
        """The #155 shape: `"actor": null` — GitHub's answer for a
        deleted account. Carried in the bucket, no phantom member."""
        saved = _make_helpers(monkeypatch, events=[
            {
                "actor": None,
                "created_at": "2024-01-01T00:00:00Z",
                "event": "assigned",
                "repo_name": "r1",
            },
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        bucket = stats[0]
        assert bucket["unattributed"] is True
        assert bucket["id"] is None
        assert bucket["total_events"] == 1
        # The bucket row carries no member identity a consumer could
        # mistake for a person — no login, no name, no averages.
        assert bucket["name"] is None
        assert "avg_weekly_activity" not in bucket
        assert "activity_period" not in bucket

    def test_issue_with_null_user_is_carried(self, monkeypatch):
        saved = _make_helpers(monkeypatch, issues=[
            {"user": None, "created_at": "2024-01-01T00:00:00Z", "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["unattributed"] is True

    def test_no_bucket_row_when_nothing_is_unattributed(self, monkeypatch):
        """A corpus without unattributed records keeps byte-identical
        output: the bucket row appears only when there is something in
        it, so existing consumers see no new shape."""
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z", "login": "alice"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert "unattributed" not in stats[0]
        assert stats[0]["id"] == "alice"

    def test_real_member_never_classified_as_unattributed(self, monkeypatch):
        """The magic-name hazard the fix removes: a login literally
        spelled 'unknown' is a real member — attributed, unmarked, never
        bucketed. The marker is a field, not a name."""
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "login": "unknown"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["id"] == "unknown"
        assert "unattributed" not in stats[0]

    def test_bucket_never_counted_as_a_member(self, monkeypatch):
        """Member totals count only members: alice keeps her own commit,
        the bucket keeps the unattributed one, and no member row absorbs
        it."""
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z", "login": "alice"}},
             "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-02T00:00:00Z"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        members = [s for s in stats if not s.get("unattributed")]
        buckets = [s for s in stats if s.get("unattributed")]
        assert len(members) == 1
        assert len(buckets) == 1
        assert members[0]["id"] == "alice"
        assert members[0]["total_commits"] == 1
        assert buckets[0]["total_commits"] == 1

    def test_a_dateless_record_is_neither_member_nor_bucket(self, monkeypatch):
        """No parseable date, no identity channels: no event at all —
        the date gate precedes the bucket in this module too, which is
        what keeps Bronze `_metadata` entries out of the output."""
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"login": None, "name": None,
                                   "author_email_hash": None}},
             "repo_name": "r1"},
            {"_metadata": {"extracted_at": "2024-01-01T00:00:00Z"}},
        ])
        ms.process_members_statistics()
        assert saved["data/silver/members_statistics.json"] == []

    def test_two_unattributed_records_do_not_become_a_member(self, monkeypatch):
        """Two unattributed commits are not one contributor with two
        commits: they are each nobody-we-can-name (#154). The only thing
        counting them is the marked bucket, which carries no identity —
        no unmarked row may hold their events."""
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z"}},
             "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-02-01T00:00:00Z"}},
             "repo_name": "r2"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        # No member rows at all — the two records belong to nobody.
        assert [s for s in stats if not s.get("unattributed")] == []
        buckets = [s for s in stats if s.get("unattributed")]
        assert len(buckets) == 1
        assert buckets[0]["total_commits"] == 2
        assert buckets[0]["total_events"] == 2
        assert buckets[0]["id"] is None


# ---------------------------------------------------------------------------
# Issue processing
# ---------------------------------------------------------------------------

class TestIssueProcessing:
    def test_issue_created_and_closed(self, monkeypatch):
        saved = _make_helpers(monkeypatch, issues=[
            {
                "user": {"login": "alice"},
                "created_at": "2024-01-01T00:00:00Z",
                "state": "closed",
                "closed_at": "2024-01-10T00:00:00Z",
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["total_issues_created"] == 1
        assert stats[0]["total_issues_closed"] == 1

    def test_closed_issue_uses_updated_at_fallback(self, monkeypatch):
        """When closed_at is missing, updated_at is used."""
        saved = _make_helpers(monkeypatch, issues=[
            {
                "user": {"login": "alice"},
                "created_at": "2024-01-01T00:00:00Z",
                "state": "closed",
                "updated_at": "2024-01-05T00:00:00Z",
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["total_issues_closed"] == 1


# ---------------------------------------------------------------------------
# PR processing
# ---------------------------------------------------------------------------

class TestPRProcessing:
    def test_pr_created_and_closed(self, monkeypatch):
        saved = _make_helpers(monkeypatch, prs=[
            {
                "user": {"login": "bob"},
                "created_at": "2024-02-01T00:00:00Z",
                "state": "closed",
                "closed_at": "2024-02-15T00:00:00Z",
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["total_prs_created"] == 1
        assert stats[0]["total_prs_closed"] == 1


# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------

class TestEventProcessing:
    def test_comment_event_counted(self, monkeypatch):
        saved = _make_helpers(monkeypatch, events=[
            {
                "actor": {"login": "alice"},
                "created_at": "2024-03-01T00:00:00Z",
                "event": "commented",
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["total_comments"] == 1

    def test_non_comment_event_not_counted(self, monkeypatch):
        saved = _make_helpers(monkeypatch, events=[
            {
                "actor": {"login": "alice"},
                "created_at": "2024-03-01T00:00:00Z",
                "event": "labeled",
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["total_comments"] == 0
        assert stats[0]["total_events"] == 1

    def test_repos_deduplicated_across_events(self, monkeypatch):
        saved = _make_helpers(monkeypatch, events=[
            {"actor": {"login": "alice"}, "created_at": "2024-03-01T00:00:00Z",
             "event": "commented", "repo_name": "r1"},
            {"actor": {"login": "alice"}, "created_at": "2024-03-02T00:00:00Z",
             "event": "commented", "repo_name": "r1"},
            {"actor": {"login": "alice"}, "created_at": "2024-03-03T00:00:00Z",
             "event": "labeled", "repo_name": "r2"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["repos_count"] == 2
        assert set(stats[0]["repos"]) == {"r1", "r2"}


# ---------------------------------------------------------------------------
# Activity period & weekly averages
# ---------------------------------------------------------------------------

class TestActivityPeriod:
    def test_zero_activity_period_clamped(self, monkeypatch):
        """When first == last activity the period clamps to 0.1 weeks."""
        saved = _make_helpers(monkeypatch, commits=[
            {
                "commit": {"author": {"date": "2024-06-01T12:00:00Z"}},
                "author": {"login": "alice"},
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["activity_period"]["weeks"] == 0.1
        assert stats[0]["avg_weekly_activity"] == 10.0  # 1 event / 0.1

    def test_multi_week_period(self, monkeypatch):
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z"}},
             "author": {"login": "alice"}, "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-15T00:00:00Z"}},
             "author": {"login": "alice"}, "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["activity_period"]["days"] == 14
        assert stats[0]["activity_period"]["weeks"] == 2.0
        assert stats[0]["avg_weekly_activity"] == 1.0  # 2 events / 2 weeks


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

class TestSorting:
    def test_sorted_by_avg_weekly_activity_desc(self, monkeypatch):
        """Members are sorted by avg_weekly_activity descending."""
        saved = _make_helpers(monkeypatch, commits=[
            # alice: 1 event in 0.1 weeks → 10.0
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z"}},
             "author": {"login": "alice"}, "repo_name": "r1"},
            # bob: 2 events in 2 weeks → 1.0
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z"}},
             "author": {"login": "bob"}, "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-15T00:00:00Z"}},
             "author": {"login": "bob"}, "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["name"] == "alice"
        assert stats[1]["name"] == "bob"
        assert stats[0]["avg_weekly_activity"] > stats[1]["avg_weekly_activity"]


# ---------------------------------------------------------------------------
# Metadata stripping
# ---------------------------------------------------------------------------

class TestMetadataStripping:
    def test_metadata_stripped_from_inputs(self, monkeypatch):
        saved = _make_helpers(monkeypatch, commits=[
            {"_metadata": {"ts": "2024-01-01"}},  # should be stripped
            {"commit": {"author": {"date": "2024-06-01T00:00:00Z"}},
             "author": {"login": "alice"}, "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["name"] == "alice"


# ---------------------------------------------------------------------------
# Identity fallback: author_email_hash (issue #125)
# ---------------------------------------------------------------------------

class TestIdentityHash:
    def test_unlinked_author_hash_resolves_to_unique_identity(self, monkeypatch):
        """An unlinked commit author carrying author_email_hash resolves to a
        stable, unique identity through Silver — it is not dropped as
        'unknown'."""
        h = "a1b2c3d4" + "0" * 56
        saved = _make_helpers(monkeypatch, commits=[
            {
                "commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                      "author_email_hash": h}},
                "repo_name": "r1",
            }
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 1
        assert stats[0]["name"] != "unknown"
        assert stats[0]["name"] == f"Unknown contributor ({h[:8]})"

    def test_two_unlinked_authors_do_not_collapse(self, monkeypatch):
        """Two distinct unlinked authors must not collapse to one display
        value — the hash prefix keeps each row distinct."""
        h1 = "a1b2c3d4" + "0" * 56
        h2 = "e5f6a7b8" + "0" * 56
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h1}}, "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-02T00:00:00Z",
                                   "author_email_hash": h2}}, "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        names = [s["name"] for s in stats]
        assert len(stats) == 2
        assert len(set(names)) == 2
        assert set(names) == {
            f"Unknown contributor ({h1[:8]})",
            f"Unknown contributor ({h2[:8]})",
        }


# ---------------------------------------------------------------------------
# display_name contract (issue #125)
# ---------------------------------------------------------------------------

class TestDisplayName:
    def test_hash_renders_first_8_hex_chars(self):
        h = "a1b2c3d4" + "0" * 56
        assert ms.display_name(h) == "Unknown contributor (a1b2c3d4)"

    def test_login_passes_through_unchanged(self):
        assert ms.display_name("alice") == "alice"

    def test_never_empty_and_unique(self):
        h1 = "a1b2c3d4" + "0" * 56
        h2 = "e5f6a7b8" + "0" * 56
        assert ms.display_name(h1)
        assert ms.display_name("alice")
        assert ms.display_name(h1) != ms.display_name(h2)


# ---------------------------------------------------------------------------
# Identity key field (issue #151, step 1)
# ---------------------------------------------------------------------------

class TestIdentityId:
    """`id` is the stable identity key, distinct from the display `name`.

    Step 1 emits `id` alongside the existing `name` while leaving `name`
    untouched, so every record carries a unique, stable key independent of
    its (possibly legibility-improved-later) label.
    """

    def test_every_record_has_non_empty_id(self, monkeypatch):
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z", "login": "alice"}},
             "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-02T00:00:00Z",
                                   "author_email_hash": "a1b2c3d4" + "0" * 56}},
             "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-03T00:00:00Z", "name": "Charlie"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 3
        for record in stats:
            assert record.get("id")

    def test_id_equals_chain_when_login_present(self, monkeypatch):
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z", "login": "alice"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["id"] == "alice"

    def test_id_equals_chain_when_hash_present(self, monkeypatch):
        h = "a1b2c3d4" + "0" * 56
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["id"] == h

    def test_id_equals_chain_when_only_name_present(self, monkeypatch):
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z", "name": "Charlie"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["id"] == "Charlie"

    def test_shared_name_distinct_ids_do_not_collapse(self, monkeypatch):
        """Two identities sharing one name string stay two records with distinct
        ids — the hash keeps them apart even though the display name matches."""
        h1 = "a1b2c3d4" + "0" * 56
        h2 = "e5f6a7b8" + "0" * 56
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h1,
                                   "name": "CI/CD Bot"}},
             "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-02T00:00:00Z",
                                   "author_email_hash": h2,
                                   "name": "CI/CD Bot"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        ids = [s["id"] for s in stats]
        assert len(stats) == 2
        assert len(set(ids)) == 2
        assert set(ids) == {h1, h2}


# ---------------------------------------------------------------------------
# Display label chain: login -> real name -> Unknown contributor (#151, step 3)
# ---------------------------------------------------------------------------

class TestDisplayLabel:
    """`name` becomes a legible label while `id` keeps the identity.

    Every test asserts both fields on the same record: the label chain
    (login -> real name -> Unknown contributor) applies to `name` only, and
    the identity chain (login -> author_email_hash -> name) must not move.
    """

    def test_login_identity_displays_login_even_with_real_name(self, monkeypatch):
        """A login wins over the real name observed alongside it — the label
        chain ranks login first, exactly like the identity chain."""
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "login": "alice",
                                   "name": "Alice Testperson"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["id"] == "alice"
        assert stats[0]["name"] == "alice"

    def test_hash_with_real_name_displays_name_and_keys_on_hash(self, monkeypatch):
        """The bug this step fixes: a hash identity with a name recoverable
        from Bronze used to render 'Unknown contributor (…)'. The name must
        display, and the id must stay the hash — one field doing two jobs
        was the original defect."""
        h = "a1b2c3d4" + "0" * 56
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h,
                                   "name": "Bela Testperson"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["id"] == h
        assert stats[0]["name"] == "Bela Testperson"

    def test_hash_without_name_displays_unknown_contributor(self, monkeypatch):
        """No name observed anywhere: the honest fallback, built from the
        hash so distinct people never share a label."""
        h = "a1b2c3d4" + "0" * 56
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["id"] == h
        assert stats[0]["name"] == "Unknown contributor (a1b2c3d4)"

    def test_two_identities_one_display_name_stay_two_records(self, monkeypatch):
        """A label may repeat; the identity must not. Two people whose name
        is 'CI/CD Bot' stay two records with distinct ids and the SAME
        name — the count is the assertion."""
        h1 = "a1b2c3d4" + "0" * 56
        h2 = "e5f6a7b8" + "0" * 56
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h1,
                                   "name": "CI/CD Bot"}},
             "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-02T00:00:00Z",
                                   "author_email_hash": h2,
                                   "name": "CI/CD Bot"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert len(stats) == 2
        assert {s["id"] for s in stats} == {h1, h2}
        assert [s["name"] for s in stats] == ["CI/CD Bot", "CI/CD Bot"]

    def test_spaced_spelling_beats_more_frequent_unspaced(self, monkeypatch):
        """Space beats frequency: 1 occurrence of 'Renato Britto Araujo'
        wins over 172 of 'RenatoBrittoAraujo' (the spellings and counts are
        the measured corpus case from #151). Decided at aggregation, so the
        winner needs every event seen first."""
        h = "a1b2c3d4" + "0" * 56
        commits = [
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h,
                                   "name": "Renato Britto Araujo"}},
             "repo_name": "r1"},
        ]
        commits += [
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h,
                                   "name": "RenatoBrittoAraujo"}},
             "repo_name": "r1"}
            for _ in range(172)
        ]
        saved = _make_helpers(monkeypatch, commits=commits)
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["id"] == h
        assert stats[0]["name"] == "Renato Britto Araujo"

    def test_equal_frequency_spaced_spellings_break_lexicographically(self, monkeypatch):
        """Two space-containing spellings at equal frequency: the winner is
        the lexicographically smaller one. The later spelling is inserted
        FIRST, so only the tie-break — not dict order — can pick
        'Alpha Testname'."""
        h = "a1b2c3d4" + "0" * 56
        saved = _make_helpers(monkeypatch, commits=[
            {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                   "author_email_hash": h,
                                   "name": "Beta Testname"}},
             "repo_name": "r1"},
            {"commit": {"author": {"date": "2024-01-02T00:00:00Z",
                                   "author_email_hash": h,
                                   "name": "Alpha Testname"}},
             "repo_name": "r1"},
        ])
        ms.process_members_statistics()
        stats = saved["data/silver/members_statistics.json"]
        assert stats[0]["name"] == "Alpha Testname"


class TestDisplayNameLabelChain:
    """Unit contract of display_name(identifier, name_counts)."""

    def test_hash_with_observed_name_renders_the_name(self):
        h = "a1b2c3d4" + "0" * 56
        assert ms.display_name(h, {"Bela Testperson": 4}) == "Bela Testperson"

    def test_hash_without_observed_name_renders_unknown(self):
        h = "a1b2c3d4" + "0" * 56
        assert ms.display_name(h) == "Unknown contributor (a1b2c3d4)"
        assert ms.display_name(h, {}) == "Unknown contributor (a1b2c3d4)"

    def test_login_ignores_observed_names(self):
        assert ms.display_name("alice", {"Alice Testperson": 9}) == "alice"


class TestChooseDisplaySpelling:
    """The deterministic spelling rule, each step pinned on its own."""

    def test_empty_observation_returns_none(self):
        assert ms.choose_display_spelling({}) is None

    def test_space_beats_frequency(self):
        counts = {"RenatoBrittoAraujo": 172, "Renato Britto Araujo": 1}
        assert ms.choose_display_spelling(counts) == "Renato Britto Araujo"

    def test_frequency_decides_when_no_spelling_has_a_space(self):
        counts = {"analytics-bot": 100, "analytics": 258}
        assert ms.choose_display_spelling(counts) == "analytics"

    def test_frequency_decides_among_spaced_spellings(self):
        counts = {"Hugo Testname": 194, "Hugo Other Testname": 19}
        assert ms.choose_display_spelling(counts) == "Hugo Testname"

    def test_tie_breaks_lexicographically_regardless_of_insertion_order(self):
        counts = {}
        counts["Zed Testname"] = 3
        counts["Ada Testname"] = 3
        assert ms.choose_display_spelling(counts) == "Ada Testname"
