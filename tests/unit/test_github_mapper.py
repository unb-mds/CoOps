"""GitHub REST/GraphQL payloads -> domain models (issue #25).

Every fixture below is shaped like the provider's real response (field
names and nesting taken from the corpus and from the queries in
``coops.utils.github_api``) but every value is invented: this file is
committed to a public repository, so no real login, name or address
appears in it. Addresses use the reserved example domains exclusively.

The two combinations ``docs/definition-of-done.md`` warns about — the ones
isolated per-channel fixtures cannot see — are here on purpose and say so
in their docstrings:

1. an author carrying **both** a legible name and an email hash (so the
   ``login -> hash -> name`` ordering is observable), and
2. two authors sharing one name with different hashes (so the models'
   refusal to merge them is observable).
"""

import hashlib
from dataclasses import fields

import pytest

from coops.bronze.commits import _hash_email as bronze_hash_email
from coops.domain import PROVIDER_GITHUB, ProviderAccount, TenantId
from coops.github.mapper import (
    _hash_email,
    map_activity_event,
    map_commit_graphql,
    map_commit_rest,
    map_file_tree_graphql,
    map_file_tree_rest,
    map_issue,
    map_member,
    map_pull_request,
    map_repository,
)

TENANT = TenantId("test-org")
# The GitHub account of the same organization: where the records come from.
ACCOUNT = ProviderAccount(PROVIDER_GITHUB, "test-org")

SHA = "a" * 40
PARENT_SHA = "b" * 40
# Synthetic address; its hash below is derived from it in the test that
# asserts it, so the pin travels with the derivation.
ADDRESS = "rosa.almeida@example.com"
ADDRESS_HASH = hashlib.sha256(ADDRESS.encode("utf-8")).hexdigest()
OTHER_ADDRESS = "ci.bot@example.org"
OTHER_HASH = hashlib.sha256(OTHER_ADDRESS.encode("utf-8")).hexdigest()


def graphql_history_node(**over):
    """One node of ``history.nodes[]`` as ``graphql_commit_history`` requests it."""
    node = {
        "oid": SHA,
        "message": "Refactor the capture loop",
        "messageHeadline": "Refactor the capture loop",
        "committedDate": "2026-03-04T10:00:00Z",
        "author": {
            "name": "Rosa Almeida",
            "email": ADDRESS,
            "user": {"login": "rosa-almeida", "databaseId": 1001},
        },
        "committer": {
            "name": "Rosa Almeida",
            "email": ADDRESS,
            "date": "2026-03-04T10:00:00Z",
        },
        "additions": 12,
        "deletions": 4,
        "parents": {"nodes": [{"oid": PARENT_SHA}]},
    }
    node.update(over)
    return node


def rest_commit(**over):
    """One REST commit object: list item of /repos/{full}/commits plus stats."""
    raw = {
        "sha": SHA,
        "author": {"login": "rosa-almeida", "id": 1001},
        "commit": {
            "author": {
                "name": "Rosa Almeida",
                "email": ADDRESS,
                "date": "2026-03-03T09:00:00Z",
            },
            "committer": {
                "name": "Rosa Almeida",
                "email": ADDRESS,
                "date": "2026-03-04T10:00:00Z",
            },
            "message": "Refactor the capture loop",
            "verification": {"verified": False},
        },
        "parents": [{"sha": PARENT_SHA}],
        "stats": {"additions": 12, "deletions": 4, "total": 16},
    }
    raw.update(over)
    return raw


# --- commits: GraphQL shape -------------------------------------------------


def test_graphql_commit_maps_provider_fields():
    commit = map_commit_graphql(graphql_history_node(), TENANT, ACCOUNT, "coops")
    assert commit.tenant_id == TENANT
    assert commit.account == ACCOUNT
    assert commit.external_id == commit.sha
    assert commit.repo_name == "coops"
    assert commit.sha == SHA
    assert commit.message.startswith("Refactor the capture loop")
    assert commit.committed_at == "2026-03-04T10:00:00Z"
    assert commit.parents == (PARENT_SHA,)
    assert commit.additions == 12
    assert commit.deletions == 4
    # The GraphQL query does not request authoredDate.
    assert commit.authored_at is None


def test_graphql_commit_linked_author():
    commit = map_commit_graphql(graphql_history_node(), TENANT, ACCOUNT, "coops")
    assert commit.author.login == "rosa-almeida"
    assert commit.author.account_id == 1001
    assert commit.author.identity == "rosa-almeida"
    assert commit.author.display_name == "Rosa Almeida"
    # #101/#171: a linked author now carries the hash TOO. This assertion was
    # `email_hash is None` and is deliberately inverted, not relaxed — it
    # encoded the gate that kept the login space and the hash space disjoint,
    # which is the defect being fixed.
    #
    # The identity assertion two lines up is the one that matters here: the
    # hash is an ADDITIONAL channel, not a competing key. `identity_key`
    # resolves login -> email_hash -> name, so a linked author still keys on
    # its login and nothing downstream re-buckets.
    assert commit.author.email_hash == ADDRESS_HASH


def test_the_same_address_hashes_alike_linked_or_not():
    """The join #171 needs, at the domain boundary.

    Measured over all 130,186 commits before this change: 123,562 records had
    a login and no hash, 6,614 a hash and no login, and **zero had both** — so
    nothing could tell that a hash and a login belonged to one person, and
    1,311 linked people plus 231 unlinked hashes were counted as 1,542
    contributors with no way to reduce it.
    """
    linked = map_commit_graphql(graphql_history_node(), TENANT, ACCOUNT, "coops")

    unlinked_node = graphql_history_node()
    unlinked_node["author"] = {"name": "Rosa Almeida", "email": ADDRESS, "user": None}
    unlinked = map_commit_graphql(unlinked_node, TENANT, ACCOUNT, "coops")

    # different identities, because one has an account and one does not...
    assert linked.author.identity == "rosa-almeida"
    assert unlinked.author.identity == ADDRESS_HASH
    # ...and now one shared channel that says they are the same human
    assert linked.author.email_hash == unlinked.author.email_hash == ADDRESS_HASH


def test_graphql_commit_unlinked_author_identity_is_email_hash():
    """Real corpus shape (5.1% of authors): ``user`` is null, no account.

    Only the email identifies them, and only its hash is carried.
    """
    node = graphql_history_node()
    node["author"] = {"name": "Rosa Almeida", "email": ADDRESS, "user": None}
    commit = map_commit_graphql(node, TENANT, ACCOUNT, "coops")
    assert commit.author.login is None
    assert commit.author.identity == ADDRESS_HASH
    assert commit.author.email_hash == ADDRESS_HASH
    assert commit.author.display_name == "Rosa Almeida"


def test_graphql_commit_name_and_hash_identity_takes_hash_display_keeps_name():
    """Required combination (definition-of-done: precedence needs the pair).

    An author with a legible name AND an email hash, no login: the hash is
    the identity (a name is shared by several distinct people in the real
    corpus), the name stays the label. Asserting the pair — not each channel
    alone — is the only way to see the ordering.
    """
    node = graphql_history_node()
    node["author"] = {"name": "Rosa Almeida", "email": ADDRESS, "user": None}
    author = map_commit_graphql(node, TENANT, ACCOUNT, "coops").author
    assert author.identity == ADDRESS_HASH
    assert author.display_name == "Rosa Almeida"


def test_graphql_commit_address_shaped_name_is_blanked():
    """Real corpus shape (149 names): git user.name set to the address.

    The name becomes ``display_name=None`` (#132) — never a placeholder —
    and the hashed email remains the identity.
    """
    node = graphql_history_node()
    node["author"] = {"name": ADDRESS, "email": ADDRESS, "user": None}
    author = map_commit_graphql(node, TENANT, ACCOUNT, "coops").author
    assert author.display_name is None
    assert author.identity == ADDRESS_HASH


def test_graphql_commit_author_with_no_identifier_is_absent():
    """Real corpus shape — not an edge case: #154 measured 1,038 commits
    whose author has *no* identifier at all (no login, no account id, no
    name, no email — a deleted account, or author metadata that never
    resolved).

    The call must not raise: the commit maps and its author is **absent**
    (``None``), the same treatment the 957 null event actors get — never
    an ``Actor`` carrying a blank field, which a shared empty identity
    would merge distinct people into (#151). ``Actor.resolve`` keeps
    raising on an empty identity; the mapper decides absence first.
    """
    node = graphql_history_node()
    node["author"] = {"name": None, "email": None, "user": None}
    commit = map_commit_graphql(node, TENANT, ACCOUNT, "coops")  # no exception
    assert commit.author is None
    # The commit itself still maps in full.
    assert commit.sha == SHA
    assert commit.committed_at == "2026-03-04T10:00:00Z"


# --- commits: REST shape ------------------------------------------------------


def test_rest_commit_maps_provider_fields():
    commit = map_commit_rest(rest_commit(), TENANT, ACCOUNT, "coops")
    assert commit.sha == SHA
    assert commit.parents == (PARENT_SHA,)
    assert commit.additions == 12
    assert commit.deletions == 4
    # committed_at is the committer date; authored_at the author date.
    assert commit.committed_at == "2026-03-04T10:00:00Z"
    assert commit.authored_at == "2026-03-03T09:00:00Z"


def test_rest_commit_list_item_without_stats_maps_to_none():
    raw = rest_commit()
    del raw["stats"]
    commit = map_commit_rest(raw, TENANT, ACCOUNT, "coops")
    assert commit.additions is None
    assert commit.deletions is None


def test_rest_commit_unlinked_author_identity_is_email_hash():
    """Real corpus shape: top-level ``author`` is null (no account link)."""
    raw = rest_commit()
    raw["author"] = None
    author = map_commit_rest(raw, TENANT, ACCOUNT, "coops").author
    assert author.login is None
    assert author.identity == ADDRESS_HASH
    assert author.display_name == "Rosa Almeida"


def test_rest_commit_name_and_hash_identity_takes_hash_display_keeps_name():
    """The precedence pair, on the REST shape too (see the GraphQL twin)."""
    raw = rest_commit()
    raw["author"] = None
    author = map_commit_rest(raw, TENANT, ACCOUNT, "coops").author
    assert author.identity == ADDRESS_HASH
    assert author.display_name == "Rosa Almeida"


def test_rest_commit_author_with_no_identifier_is_absent():
    """REST twin of the #154 shape (1,038 commits in local-run): the
    top-level ``author`` is null (no account link) and the git identity
    carries no name and no email — all four identifiers absent.

    The call must not raise: the commit maps with ``author=None``, exactly
    as the GraphQL mapper does for the same author.
    """
    raw = rest_commit()
    raw["author"] = None
    raw["commit"]["author"] = {"name": None, "email": None, "date": None}
    commit = map_commit_rest(raw, TENANT, ACCOUNT, "coops")  # no exception
    assert commit.author is None
    assert commit.authored_at is None  # the absent author left no date either
    assert commit.committed_at == "2026-03-04T10:00:00Z"


def test_rest_commit_login_falls_back_to_commit_author_when_top_level_absent():
    """The Bronze corpus shape (#168): ``_sanitize_commit`` writes the
    account link inside ``commit.author`` (``login``/``id`` next to the
    git identity) and leaves no top-level ``author`` at all — measured,
    0 of 28,244 local-run and 0 of 130,186 fga records carry a top-level
    login, while 27,206 / 123,562 carry one inside. The top-level-only
    read never saw it.
    """
    raw = rest_commit()
    del raw["author"]
    raw["commit"]["author"] = {
        "name": "Rosa Almeida",
        "email": ADDRESS,
        "date": "2026-03-03T09:00:00Z",
        "login": "rosa-almeida",
        "id": 1001,
    }
    author = map_commit_rest(raw, TENANT, ACCOUNT, "coops").author
    assert author.login == "rosa-almeida"
    assert author.account_id == 1001
    # The login outranks name and email hash: it is the account link.
    assert author.identity == "rosa-almeida"


def test_rest_commit_login_inside_commit_author_with_null_name_still_attributes():
    """The local-run corpus shape exactly: ``name`` null, no ``id``, no
    top-level ``author`` — only a login inside ``commit.author``. Before
    the fallback this was the worst case: no channel resolved at all, so
    every one of these records mapped with ``author=None`` (measured,
    #168: 0 of 28,244 attributed) with a valid login one key away.
    """
    raw = rest_commit()
    del raw["author"]
    raw["commit"]["author"] = {
        "name": None,
        "email": None,
        "date": "2026-03-03T09:00:00Z",
        "login": "rosa-almeida",
    }
    author = map_commit_rest(raw, TENANT, ACCOUNT, "coops").author
    assert author.login == "rosa-almeida"
    assert author.identity == "rosa-almeida"
    assert author.display_name is None
    assert author.account_id is None


def test_rest_commit_top_level_login_beats_commit_author_login_when_both_present():
    """The other half of the fallback pair (docs/definition-of-done.md:
    precedence only exists when two values are present at once): a live
    REST response carries the account link at the top level **and** the
    record carries a Bronze-style one inside ``commit.author`` — the top
    level wins, for the login and for the account id, mirroring the
    precedence ``_sanitize_commit`` itself applies when writing.
    """
    raw = rest_commit()
    raw["commit"]["author"] = {
        "name": "Rosa Almeida",
        "email": ADDRESS,
        "date": "2026-03-03T09:00:00Z",
        "login": "not-the-account-login",
        "id": 9999,
    }
    author = map_commit_rest(raw, TENANT, ACCOUNT, "coops").author
    assert author.login == "rosa-almeida"
    assert author.account_id == 1001
    assert author.identity == "rosa-almeida"


def test_rest_commit_author_date_falls_back_when_committer_absent():
    """Real pre-#128 Bronze shape (#168): ``commit.{author, message}`` and
    **no ``committer`` key at all** — measured, 100% of one corpus's
    28,244 records, every one of which raised on the committer-only read.

    The author's date stands in for ``committed_at``, exactly how Bronze
    itself writes the pair (``author.get('date') or committed_date``).
    With this fixture's *distinct* author/committer dates, the assert can
    tell the fallback from a committer read: 2026-03-03 is the author's.
    """
    raw = rest_commit()
    del raw["commit"]["committer"]
    commit = map_commit_rest(raw, TENANT, ACCOUNT, "coops")
    assert commit.committed_at == "2026-03-03T09:00:00Z"
    assert commit.authored_at == "2026-03-03T09:00:00Z"


def test_rest_commit_committer_date_beats_author_date_when_both_present():
    """The other half of the ``committer.date or author.date`` pair
    (docs/definition-of-done.md: a chain is only observable with both
    alternatives present): when both dates exist the committer's wins —
    it is the timestamp of record for the history fetched.
    """
    commit = map_commit_rest(rest_commit(), TENANT, ACCOUNT, "coops")
    assert commit.committed_at == "2026-03-04T10:00:00Z"
    assert commit.authored_at == "2026-03-03T09:00:00Z"


def test_rest_commit_raises_when_neither_date_exists():
    """No committer date and no author date: the mapper must hand the
    model nothing, and ``Commit``'s guard must fire — the fix resolves a
    date when one is available one key away, it does not invent one
    (#168: keep the guard raising on a genuinely empty ``committed_at``).
    """
    raw = rest_commit()
    del raw["commit"]["committer"]
    raw["commit"]["author"] = {"name": "Rosa Almeida", "email": ADDRESS, "date": None}
    with pytest.raises(ValueError, match="non-empty committed_at"):
        map_commit_rest(raw, TENANT, ACCOUNT, "coops")


def test_rest_and_graphql_shapes_of_one_commit_agree():
    """Both provider shapes, one model: the mapper is the seam that absorbs
    the difference."""
    rest = map_commit_rest(rest_commit(), TENANT, ACCOUNT, "coops")
    graphql = map_commit_graphql(graphql_history_node(), TENANT, ACCOUNT, "coops")
    assert rest.sha == graphql.sha
    assert rest.parents == graphql.parents
    assert rest.author == graphql.author
    assert rest.committed_at == graphql.committed_at


def test_two_authors_same_name_different_hashes_stay_two_people():
    """Required combination: one name, two hashes — the "CI/CD Bot" case.

    Deliberately synthetic values (both addresses invented): the real
    corpus has 16 name strings each shared by several distinct people, and
    identity — not the name — must keep them apart.
    """
    first = graphql_history_node()
    first["author"] = {"name": "CI Bot", "email": ADDRESS, "user": None}
    second = graphql_history_node()
    second["author"] = {"name": "CI Bot", "email": OTHER_ADDRESS, "user": None}
    authors = {
        map_commit_graphql(first, TENANT, ACCOUNT, "coops").author,
        map_commit_graphql(second, TENANT, ACCOUNT, "coops").author,
    }
    assert len(authors) == 2
    assert {a.identity for a in authors} == {ADDRESS_HASH, OTHER_HASH}
    assert all(a.display_name == "CI Bot" for a in authors)


# --- email hashing convention ---------------------------------------------------


def test_hash_email_is_case_and_whitespace_insensitive():
    assert _hash_email("  Rosa.Almeida@EXAMPLE.com ") == _hash_email(ADDRESS)


def test_hash_email_matches_the_bronze_convention():
    """The mapper's hash is the same pseudonym Bronze derives, so identities
    produced here join against Bronze's author_email_hash."""
    assert _hash_email(ADDRESS) == bronze_hash_email(ADDRESS)


# --- members ---------------------------------------------------------------------


def test_map_member_from_profile():
    raw = {
        "login": "rosa-almeida",
        "id": 1001,
        "name": "Rosa Almeida",
        "type": "User",
        "avatar_url": "https://example.com/a.png",
        "html_url": "https://example.com/rosa-almeida",
        "created_at": "2024-01-01T00:00:00Z",
    }
    member = map_member(raw, TENANT, ACCOUNT,is_org_member=True)
    assert member.identity == "rosa-almeida"
    assert member.display_name == "Rosa Almeida"
    assert member.login == "rosa-almeida"
    assert member.external_id == "1001"
    assert member.is_org_member is True
    assert member.contributions_total == 0


def test_map_member_from_contributor_item_has_no_display_name():
    """List items carry no ``name``: display_name=None is normal, not broken."""
    raw = {"login": "bruno-teixeira", "id": 1002, "contributions": 34}
    member = map_member(raw, TENANT, ACCOUNT)
    assert member.display_name is None
    assert member.contributions_total == 34
    assert member.is_org_member is False


def test_map_member_without_login_or_name_raises():
    with pytest.raises(ValueError, match="member payload"):
        map_member({"id": 1001}, TENANT, ACCOUNT)


# --- issues and pull requests ------------------------------------------------


RAW_ISSUE = {
    "id": 9002,
    "number": 42,
    "state": "open",
    "title": "Fix the thing",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "closed_at": None,
    # Free text and nested objects the provider sends but no consumer reads:
    # present in the fixture to prove the mapper never spreads them across.
    "body": "Ping me at rosa.almeida@example.com if this breaks",
    "milestone": {"title": "v1", "description": "owner: rosa.almeida@example.com"},
    "labels": [{"name": "bug"}],
    "locked": False,
    "comments": 3,
    "user": {"login": "rosa-almeida", "id": 1001, "gravatar_id": ""},
    "assignee": {"login": "bruno-teixeira", "id": 1002},
}


def test_map_issue_builds_from_named_fields_only():
    issue = map_issue(RAW_ISSUE, TENANT, ACCOUNT, "coops")
    assert {f.name for f in fields(issue)} == {
        "tenant_id",
        "account",
        "external_id",
        "repo_name",
        "number",
        "state",
        "title",
        "author",
        "assignee",
        "created_at",
        "updated_at",
        "closed_at",
    }
    assert not hasattr(issue, "body")
    assert not hasattr(issue, "milestone")


def test_map_issue_maps_the_conversation():
    issue = map_issue(RAW_ISSUE, TENANT, ACCOUNT, "coops")
    assert issue.repo_name == "coops"
    assert issue.number == 42
    assert issue.state == "open"
    assert issue.author.identity == "rosa-almeida"
    assert issue.assignee.identity == "bruno-teixeira"
    assert issue.created_at == "2026-01-01T00:00:00Z"
    assert issue.closed_at is None


def test_map_issue_deleted_account_author_is_none():
    raw = dict(RAW_ISSUE)
    raw["user"] = None
    raw["assignee"] = None
    issue = map_issue(raw, TENANT, ACCOUNT, "coops")
    assert issue.author is None
    assert issue.assignee is None


def test_map_issue_refuses_a_pull_request_payload():
    raw = dict(RAW_ISSUE)
    raw["pull_request"] = {"merged_at": None, "url": "https://example.com/pull"}
    with pytest.raises(ValueError, match="map_pull_request"):
        map_issue(raw, TENANT, ACCOUNT, "coops")


RAW_PR = dict(
    RAW_ISSUE,
    number=43,
    draft=True,
    pull_request={
        "merged_at": "2026-01-04T00:00:00Z",
        "url": "https://example.com/pull",
    },
)


def test_map_pull_request_maps_merged_at_and_draft():
    pr = map_pull_request(RAW_PR, TENANT, ACCOUNT, "coops")
    assert pr.number == 43
    assert pr.merged_at == "2026-01-04T00:00:00Z"
    assert pr.draft is True
    assert pr.author.identity == "rosa-almeida"


def test_map_pull_request_refuses_an_issue_payload():
    with pytest.raises(ValueError, match="map_issue"):
        map_pull_request(RAW_ISSUE, TENANT, ACCOUNT, "coops")


# --- activity events -----------------------------------------------------------


def test_map_activity_event_maps_fields():
    raw = {
        "id": 91,
        "event": "cross-referenced",
        "created_at": "2026-01-05T10:00:00Z",
        "actor": {"login": "bruno-teixeira", "id": 1002},
        "issue": {"number": 42},
        "commit_id": SHA,
    }
    event = map_activity_event(raw, TENANT, ACCOUNT, "coops")
    assert event.external_id == "91"
    assert event.event_type == "cross-referenced"
    assert event.actor.identity == "bruno-teixeira"
    assert event.issue_number == 42


def test_map_activity_event_null_actor_is_an_absent_actor():
    """957 event actors in the corpus are literally null (deleted accounts):
    absent, never a Member named "unknown"."""
    raw = {
        "id": 92,
        "event": "closed",
        "created_at": "2026-01-05T11:00:00Z",
        "actor": None,
        "issue": {"number": 42},
    }
    assert map_activity_event(raw, TENANT, ACCOUNT, "coops").actor is None


# --- repositories ------------------------------------------------------------------


RAW_REPO = {
    "id": 9001,
    "name": "coops",
    "full_name": "test-org/coops",
    "private": False,
    "fork": False,
    "archived": False,
    "description": "Collaboration metrics",
    "default_branch": "main",
    "language": "Python",
    "html_url": "https://example.com/test-org/coops",
    "size": 4321,
    "stargazers_count": 12,
    "forks_count": 3,
    "open_issues_count": 42,
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2026-03-01T00:00:00Z",
    "pushed_at": "2026-03-04T10:00:00Z",
    "topics": ["metrics"],
    "license": {"spdx_id": "GPL-3.0-or-later"},
}


def test_map_repository_maps_named_fields():
    repo = map_repository(RAW_REPO, TENANT, ACCOUNT)
    assert repo.external_id == "9001"
    assert repo.name == "coops"
    assert repo.full_name == "test-org/coops"
    assert repo.default_branch == "main"
    assert repo.language == "Python"
    assert repo.size_kb == 4321
    assert repo.stargazers_count == 12
    assert repo.is_fork is False
    assert repo.is_archived is False
    # Provider fields with no consumer are not carried across.
    assert not hasattr(repo, "topics")
    assert not hasattr(repo, "license")


# --- file trees -------------------------------------------------------------------


def test_map_file_tree_rest_maps_entries():
    raw = {
        "sha": SHA,
        "truncated": False,
        "tree": [
            {
                "path": "README.md",
                "type": "blob",
                "sha": PARENT_SHA,
                "mode": "100644",
                "size": 120,
            },
            {"path": "src", "type": "tree", "sha": "c" * 40, "mode": "040000"},
        ],
    }
    tree = map_file_tree_rest(raw, TENANT, ACCOUNT, "coops", branch="main")
    assert tree.sha == SHA
    assert tree.branch == "main"
    assert tree.entries[0].path == "README.md"
    assert tree.entries[0].kind == "blob"
    assert tree.entries[0].size == 120
    assert tree.entries[0].is_binary is None  # REST says nothing about binary
    assert tree.entries[1].kind == "tree"
    assert tree.entries[1].size is None


def test_map_file_tree_rest_refuses_a_truncated_response():
    raw = {"sha": SHA, "truncated": True, "tree": [{"path": "a", "type": "blob"}]}
    with pytest.raises(ValueError, match="truncated"):
        map_file_tree_rest(raw, TENANT, ACCOUNT, "coops")


def test_map_file_tree_graphql_maps_one_level():
    entries = [
        {
            "name": "README.md",
            "type": "blob",
            "mode": "100644",
            "path": "README.md",
            "extension": ".md",
            "object": {"byteSize": 120, "isBinary": False, "oid": PARENT_SHA},
        },
        {
            "name": "src",
            "type": "tree",
            "mode": "040000",
            "path": "src",
            "extension": "",
            "object": None,
        },
    ]
    tree = map_file_tree_graphql(entries, TENANT, ACCOUNT, "coops", branch="main")
    assert tree.sha is None  # no tree sha at this level
    assert tree.external_id is None  # and therefore no external_id either
    readme, src = tree.entries
    assert readme.sha == PARENT_SHA
    assert readme.size == 120
    assert readme.is_binary is False
    assert src.sha is None
    assert src.size is None


# --- tenancy wiring: account and external_id (#187) ----------------------------


def test_map_repository_rejects_an_account_from_another_provider():
    """The mapper is the GitHub adapter's own: mapping a payload fetched
    from GitHub under the tenant's GitLab account would label GitHub data
    with the wrong provider — the exact merge #92 exists to prevent.
    """
    gitlab_account = ProviderAccount("gitlab", "test-org")
    with pytest.raises(ValueError, match="GitHub records, not 'gitlab'"):
        map_repository(RAW_REPO, TENANT, gitlab_account)


def test_map_issue_rejects_an_account_from_another_provider():
    gitlab_account = ProviderAccount("gitlab", "test-org")
    with pytest.raises(ValueError, match="GitHub records, not 'gitlab'"):
        map_issue(RAW_ISSUE, TENANT, gitlab_account, "coops")


def test_map_member_leaves_person_id_unlinked():
    """person_id is reserved for #89's replacement; the GitHub mapper must
    not invent a value — not the login, not the external_id.
    """
    raw = {"login": "rosa-almeida", "id": 1001, "name": "Rosa Almeida"}
    member = map_member(raw, TENANT, ACCOUNT)
    assert member.person_id is None


def test_mapped_entities_carry_the_account_and_their_external_ids():
    """One place that asserts the tenancy triple on every mapped entity:
    tenant, provider account, and the provider's own id as a string."""
    commit = map_commit_rest(rest_commit(), TENANT, ACCOUNT, "coops")
    assert commit.tenant_id == TENANT
    assert commit.account == ACCOUNT
    assert commit.external_id == commit.sha

    issue = map_issue(RAW_ISSUE, TENANT, ACCOUNT, "coops")
    assert issue.account == ACCOUNT
    assert issue.external_id == "9002"

    event = map_activity_event(
        {"id": 91, "event": "closed", "created_at": "2026-03-05T10:00:00Z",
         "actor": None},
        TENANT,
        ACCOUNT,
        "coops",
    )
    assert event.account == ACCOUNT
    assert event.external_id == "91"
