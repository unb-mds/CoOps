"""The unattributed record, once, for both Silver modules (#154, #155).

One defect with two symptoms: a commit whose identity channels are *all*
null (measured: 2,076 of 56,488 commits, 3.7%) and an issue event with
``"actor": null`` (measured: 957 of 298,395) used to resolve to the
string ``'unknown'``, and the two Silver consumers then did opposite
things with it — ``members_statistics`` excluded it (a silent drop),
``temporal_analysis`` kept it (a phantom contributor credited with
1,081 events).

These tests pin the single answer both modules now share
(``coops.silver.unattributed``): such a record is **unattributed** —
carried, marked with a boolean field a consumer can test, identity
``None``, never a member and never dropped.

The load-bearing class here is :class:`TestBothModulesAgree`: the same
fixture through both modules must yield the same unattributed count.
That is the assertion that would have caught the original divergence —
under the old code the members side reported 0 (drop) while the temporal
side reported 3 as a user named ``unknown``.
"""

import coops.silver.members_statistics as ms
import coops.silver.temporal_analysis as temporal
from coops.silver.unattributed import (
    UNATTRIBUTED_FIELD,
    commit_author_identity,
    conversation_actor_identity,
    is_unattributed,
    mark_unattributed,
)

H1 = "a1b2c3d4" + "0" * 56
H2 = "e5f6a7b8" + "0" * 56


def _run(module, monkeypatch, bronze):
    """Run one Silver processor over in-memory Bronze fixtures."""
    def fake_load(family):
        return bronze.get(family, [])

    saved = {}

    def fake_save(data, path, timestamp=True):
        saved[path] = data
        return path

    monkeypatch.setattr(module, "load_family", fake_load)
    monkeypatch.setattr(module, "save_json_data", fake_save)
    module.process_members_statistics() if module is ms \
        else module.process_temporal_analysis()
    return saved


def _bucket_row(saved):
    """The unattributed bucket row, or None — never more than one."""
    rows = [r for r in saved["data/silver/members_statistics.json"]
            if r.get(UNATTRIBUTED_FIELD)]
    assert len(rows) <= 1
    return rows[0] if rows else None


def _marked_events(saved):
    return [e for e in saved["data/silver/temporal_events.json"]
            if is_unattributed(e)]


# ---------------------------------------------------------------------------
# The shared resolvers: one chain per record kind, None terminus
# ---------------------------------------------------------------------------

class TestCommitAuthorIdentity:
    def test_inner_login_outranks_everything(self):
        commit = {"commit": {"author": {"login": "albedo",
                                        "author_email_hash": H1,
                                        "name": "Albedo Testname"}}}
        assert commit_author_identity(commit) == "albedo"

    def test_top_level_login_is_second(self):
        commit = {"author": {"login": "lumen"},
                  "commit": {"author": {"name": "Lumen Testname"}}}
        assert commit_author_identity(commit) == "lumen"

    def test_hash_outranks_name(self):
        commit = {"commit": {"author": {"author_email_hash": H1,
                                        "name": "Zephyr Testname"}}}
        assert commit_author_identity(commit) == H1

    def test_name_is_the_last_channel(self):
        commit = {"commit": {"author": {"name": "Quixote Tester"}}}
        assert commit_author_identity(commit) == "Quixote Tester"

    def test_every_channel_null_resolves_to_none(self):
        """The #154 shape: login, name, email hash all null — None, not
        a truthy placeholder that would become a person downstream."""
        commit = {"commit": {"author": {"date": "2024-01-01T00:00:00Z",
                                        "login": None, "name": None,
                                        "author_email_hash": None}}}
        assert commit_author_identity(commit) is None

    def test_author_object_entirely_absent_resolves_to_none(self):
        assert commit_author_identity({}) is None
        assert commit_author_identity({"commit": {}}) is None
        assert commit_author_identity({"author": None, "commit": {}}) is None


class TestConversationActorIdentity:
    def test_login_outranks_name(self):
        assert conversation_actor_identity({"login": "albedo",
                                            "name": "Albedo Testname"}) == "albedo"

    def test_name_identifies_when_there_is_no_login(self):
        assert conversation_actor_identity({"name": "Lumen Testname"}) == "Lumen Testname"

    def test_null_actor_resolves_to_none(self):
        """The #155 shape: GitHub's answer for a deleted account."""
        assert conversation_actor_identity(None) is None

    def test_absent_or_empty_object_resolves_to_none(self):
        assert conversation_actor_identity({}) is None

    def test_blank_channels_resolve_to_none(self):
        assert conversation_actor_identity({"login": "", "name": ""}) is None


class TestMarker:
    def test_marks_an_event_whose_user_is_none(self):
        record = mark_unattributed({"user": None, "type": "commit"})
        assert is_unattributed(record) is True
        assert record[UNATTRIBUTED_FIELD] is True

    def test_leaves_an_attributed_event_untouched(self):
        record = mark_unattributed({"user": "albedo", "type": "commit"})
        assert is_unattributed(record) is False
        assert UNATTRIBUTED_FIELD not in record

    def test_the_marker_is_a_field_no_login_can_collide_with(self):
        """'unknown' collided with a login a real member may hold; a
        boolean field cannot."""
        assert UNATTRIBUTED_FIELD == "unattributed"


# ---------------------------------------------------------------------------
# temporal_analysis: carried, marked, never a phantom member
# ---------------------------------------------------------------------------

class TestTemporalCarriesUnattributed:
    def test_commit_with_every_channel_null_is_carried_marked(self, monkeypatch):
        """Not a user named 'unknown' (the old phantom) and not a silent
        drop: one event, user null, marked."""
        saved = _run(temporal, monkeypatch, {"commits": [
            {"repo_name": "alpha-api",
             "commit": {"author": {"date": "2024-01-02T11:00:00Z",
                                   "login": None, "name": None,
                                   "author_email_hash": None}}},
        ]})
        events = saved["data/silver/temporal_events.json"]
        assert len(events) == 1
        assert events[0]["user"] is None
        assert is_unattributed(events[0]) is True
        # The marker is the test a consumer uses; the identity is absent.
        assert events[0][UNATTRIBUTED_FIELD] is True

    def test_null_actor_event_is_carried_marked(self, monkeypatch):
        """The #155 shape: `"actor": null` — carried, not credited to a
        contributor who does not exist."""
        saved = _run(temporal, monkeypatch, {"issue_events": [
            {"repo_name": "delta-docs", "event": "assigned",
             "created_at": "2024-01-07T10:00:00Z", "actor": None},
        ]})
        events = saved["data/silver/temporal_events.json"]
        assert len(events) == 1
        assert events[0]["user"] is None
        assert is_unattributed(events[0]) is True

    def test_no_event_user_is_the_string_unknown(self, monkeypatch):
        """No user-facing label is the bare string 'unknown' (#155
        acceptance): it collides with a legitimate name and reads as a
        person."""
        saved = _run(temporal, monkeypatch, {
            "commits": [{"repo_name": "r1",
                         "commit": {"author": {"date": "2024-01-01T00:00:00Z"}}}],
            "issue_events": [{"repo_name": "r1", "event": "closed",
                              "created_at": "2024-01-02T00:00:00Z",
                              "actor": None}],
        })
        users = {e["user"] for e in saved["data/silver/temporal_events.json"]}
        assert "unknown" not in users

    def test_a_login_literally_spelled_unknown_is_attributed(self, monkeypatch):
        """The magic-name hazard: a real member whose login is 'unknown'
        keeps their events — attributed, unmarked."""
        saved = _run(temporal, monkeypatch, {"commits": [
            {"repo_name": "r1", "commit": {"author": {
                "date": "2024-01-01T00:00:00Z", "login": "unknown"}}},
        ]})
        events = saved["data/silver/temporal_events.json"]
        assert events[0]["user"] == "unknown"
        assert is_unattributed(events[0]) is False

    def test_a_dateless_record_is_not_carried_even_unattributed(self, monkeypatch):
        """A record with no parseable date cannot be placed in time, so
        it is no event at all — not a member's, and not the bucket's
        either. The date gate precedes the bucket in both modules, so a
        Bronze `_metadata` entry (dateless, identity-less) lands in no
        output."""
        saved = _run(temporal, monkeypatch, {"commits": [
            {"repo_name": "r1", "commit": {"author": {
                "login": None, "name": None, "author_email_hash": None}}},
            {"_metadata": {"extracted_at": "2024-01-01T00:00:00Z"}},
            {"repo_name": "r1", "commit": {"author": {
                "date": "2024-01-01T00:00:00Z", "login": "albedo"}}},
        ]})
        events = saved["data/silver/temporal_events.json"]
        assert len(events) == 1
        assert events[0]["user"] == "albedo"
        assert not is_unattributed(events[0])
        daily = saved["data/silver/daily_activity_summary.json"]
        assert sum(d["unattributed_events"] for d in daily) == 0


class TestTemporalDailySummary:
    def test_day_totals_count_them_but_no_author_holds_them(self, monkeypatch):
        """One day, one real member, one unattributed commit: the day's
        totals count both, but only alice appears as a user or an
        author, and `unattributed_events` reconciles the difference."""
        saved = _run(temporal, monkeypatch, {"commits": [
            {"repo_name": "r1", "commit": {"author": {
                "date": "2024-03-01T10:00:00Z", "login": "albedo"}}},
            {"repo_name": "r1", "commit": {"author": {
                "date": "2024-03-01T11:00:00Z"}}},
        ]})
        day = saved["data/silver/daily_activity_summary.json"][0]
        assert day["total_events"] == 2
        assert day["commits"] == 2
        assert day["unique_users"] == 1
        assert day["unattributed_events"] == 1
        assert [a["id"] for a in day["authors"]] == ["albedo"]

    def test_two_unattributed_events_merge_into_no_person(self, monkeypatch):
        """Two unattributed events are not one contributor with two
        events: no author entry, no user key, nothing to group them
        under — only the day-level count, which is a property of the
        day, not of an entity."""
        saved = _run(temporal, monkeypatch, {"commits": [
            {"repo_name": "r1", "commit": {"author": {
                "date": "2024-03-01T10:00:00Z"}}},
            {"repo_name": "r2", "commit": {"author": {
                "date": "2024-03-01T11:00:00Z"}}},
        ]})
        events = saved["data/silver/temporal_events.json"]
        assert len(events) == 2
        assert all(e["user"] is None and is_unattributed(e) for e in events)

        day = saved["data/silver/daily_activity_summary.json"][0]
        assert day["total_events"] == 2
        assert day["unique_users"] == 0
        assert day["unattributed_events"] == 2
        assert day["authors"] == []

    def test_activity_heatmap_still_counts_them(self, monkeypatch):
        """Carried means carried: unattributed activity stays in the
        heatmap, which is a repository-level view and keys on nobody."""
        saved = _run(temporal, monkeypatch, {"commits": [
            {"repo_name": "r1", "commit": {"author": {
                "date": "2024-03-01T10:00:00Z", "login": "albedo"}}},
            {"repo_name": "r1", "commit": {"author": {
                "date": "2024-03-01T11:00:00Z"}}},
        ]})
        heatmap = saved["data/silver/activity_heatmap.json"]
        assert sum(cell["activity_count"] for cell in heatmap) == 2


# ---------------------------------------------------------------------------
# members_statistics: carried, marked, never a member
# ---------------------------------------------------------------------------

class TestMembersCarryUnattributed:
    def test_bucket_row_reports_the_count(self, monkeypatch):
        saved = _run(ms, monkeypatch, {"commits": [
            {"repo_name": "r1", "commit": {"author": {
                "date": "2024-01-01T00:00:00Z"}}},
            {"repo_name": "r2", "commit": {"author": {
                "date": "2024-01-02T00:00:00Z"}}},
        ]})
        stats = saved["data/silver/members_statistics.json"]
        bucket = _bucket_row(saved)
        assert bucket is not None
        assert bucket["total_commits"] == 2
        assert bucket["repos"] == ["r1", "r2"]
        # The bucket is the only row; nobody absorbed the records.
        assert len(stats) == 1


# ---------------------------------------------------------------------------
# The two modules agree — the assertion that would have caught the
# original divergence
# ---------------------------------------------------------------------------

#: One mixed corpus, hand-counted: three unattributable records (one
#: commit with every channel null, one issue with `user: null`, one
#: event with `actor: null`) and five attributed ones. The counts below
#: are the independent oracle — derived by enumerating the fixture, not
#: by re-running the code under test.
BRONZE = {
    "commits": [
        {"repo_name": "alpha-api", "commit": {"author": {
            "date": "2024-01-02T10:00:00Z", "login": "albedo"}}},
        {"repo_name": "alpha-api", "commit": {"author": {
            "date": "2024-01-02T11:00:00Z", "login": None, "name": None,
            "author_email_hash": None}}},
        {"repo_name": "nova-web", "commit": {"author": {
            "date": "2024-01-03T09:00:00Z", "author_email_hash": H1}}},
    ],
    "issues": [
        {"repo_name": "nova-web", "created_at": "2024-01-04T10:00:00Z",
         "user": {"login": "lumen"}},
        {"repo_name": "nova-web", "created_at": "2024-01-05T10:00:00Z",
         "user": None},
    ],
    "prs": [
        {"repo_name": "delta-docs", "created_at": "2024-01-06T10:00:00Z",
         "user": {"name": "Quixote Tester"}},
    ],
    "issue_events": [
        {"repo_name": "delta-docs", "event": "assigned",
         "created_at": "2024-01-07T10:00:00Z", "actor": None},
        {"repo_name": "delta-docs", "event": "labeled",
         "created_at": "2024-01-07T11:00:00Z", "actor": {"login": "zephyr"}},
    ],
}
UNATTRIBUTED_IN_FIXTURE = 3  # the null-channel commit, the null-user issue, the null-actor event
ATTRIBUTED_IN_FIXTURE = 5    # albedo, H1, lumen, Quixote Tester, zephyr


class TestBothModulesAgree:
    """The same input through both modules yields the same count of
    unattributed records — and that count is the one the input carries.

    Under the old code this fails twice over: members_statistics dropped
    the records (bucket count 0, not 3) and temporal_analysis attributed
    them to a phantom user 'unknown' (marked count 0, not 3).
    """

    def test_same_fixture_same_unattributed_count(self, monkeypatch):
        temporal_saved = _run(temporal, monkeypatch, BRONZE)
        members_saved = _run(ms, monkeypatch, BRONZE)

        temporal_count = len(_marked_events(temporal_saved))
        bucket = _bucket_row(members_saved)
        members_count = bucket["total_events"] if bucket else 0

        # Each side against the hand-counted oracle…
        assert temporal_count == UNATTRIBUTED_IN_FIXTURE
        assert members_count == UNATTRIBUTED_IN_FIXTURE
        # …and the two sides against each other.
        assert temporal_count == members_count

    def test_nothing_is_silently_dropped_either_side(self, monkeypatch):
        """Carried, not dropped: every record the fixture carries
        survives into temporal_events, and the members side accounts for
        attributed + unattributed with no gap."""
        temporal_saved = _run(temporal, monkeypatch, BRONZE)
        members_saved = _run(ms, monkeypatch, BRONZE)

        events = temporal_saved["data/silver/temporal_events.json"]
        assert len(events) == UNATTRIBUTED_IN_FIXTURE + ATTRIBUTED_IN_FIXTURE

        stats = members_saved["data/silver/members_statistics.json"]
        members = [s for s in stats if not s.get(UNATTRIBUTED_FIELD)]
        bucket = _bucket_row(members_saved)
        assert len(members) == ATTRIBUTED_IN_FIXTURE
        assert sum(m["total_events"] for m in members) == ATTRIBUTED_IN_FIXTURE
        assert bucket["total_events"] == UNATTRIBUTED_IN_FIXTURE

    def test_attributed_records_never_land_in_the_bucket(self, monkeypatch):
        """Every identity channel that exists keeps its record out of
        the bucket, on both sides: a login, an email hash and a name are
        each enough to attribute."""
        temporal_saved = _run(temporal, monkeypatch, BRONZE)
        members_saved = _run(ms, monkeypatch, BRONZE)

        attributed_users = {e["user"] for e in
                            temporal_saved["data/silver/temporal_events.json"]
                            if not is_unattributed(e)}
        assert attributed_users == {"albedo", H1, "lumen",
                                    "Quixote Tester", "zephyr"}
        members = [s for s in
                   members_saved["data/silver/members_statistics.json"]
                   if not s.get(UNATTRIBUTED_FIELD)]
        assert {m["id"] for m in members} == attributed_users

    def test_the_marker_is_what_distinguishes_not_a_name(self, monkeypatch):
        """A consumer of either artifact separates the bucket from the
        members by testing the field — never by matching a name."""
        temporal_saved = _run(temporal, monkeypatch, BRONZE)
        members_saved = _run(ms, monkeypatch, BRONZE)

        for event in temporal_saved["data/silver/temporal_events.json"]:
            if is_unattributed(event):
                assert event["user"] is None
            else:
                assert isinstance(event["user"], str)

        bucket = _bucket_row(members_saved)
        members = [s for s in
                   members_saved["data/silver/members_statistics.json"]
                   if not s.get(UNATTRIBUTED_FIELD)]
        assert bucket[UNATTRIBUTED_FIELD] is True
        assert bucket["id"] is None
        assert all(m["id"] for m in members)
