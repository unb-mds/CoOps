"""GitHub REST/GraphQL payloads -> domain models (issue #25).

This module is the only place outside the extractors that knows about
GitHub: it reads the provider's own shapes and builds
:class:`~coops.domain` models from **named fields**, never by spreading a
response. ``data/bronze/`` is a publish boundary downstream of this mapper,
and a whitelist is bounded by what we use while a denylist is bounded by
what we have noticed (``docs/definition-of-done.md``; the projection pattern
is ``_project_issue``/``ISSUE_FIELDS`` in :mod:`coops.bronze.issues`).
Building a dataclass from named keyword arguments makes spreading
impossible by construction: a provider field nobody whitelisted raises
``TypeError`` instead of leaking onto the model.

Commit authors arrive in two shapes, and both are mapped here from the
*provider* representation rather than from the REST-like intermediate that
:mod:`coops.bronze.commits` normalises into — so the GitHub knowledge stays
in one place:

- GraphQL ``history`` nodes, as requested by
  ``GitHubAPIClient.graphql_commit_history``::

      {oid, message, committedDate,
       author {name email user {login databaseId}},
       parents {nodes {oid}}, additions, deletions}

- REST commit objects (list items of ``/repos/{full_name}/commits`` or the
  commit detail)::

      {sha, commit {author {name email date}, committer {date}?, message},
       author {login id} | null, parents [{sha}], stats {additions deletions}}

  where ``committer`` may be absent entirely (pre-#128 Bronze records,
  #168) and the author's date stands in for ``committed_at``. The account
  link sits in the top-level ``author`` on the live response; in the
  Bronze records ``_sanitize_commit`` writes it moves inside
  ``commit.author`` (``login``/``id``), which this mapper reads as a
  fallback when the top level carries none — the top level wins when both
  are present.

An email address never reaches a model. As in
:func:`coops.bronze.commits._sanitize_commit`, the address is reduced to its
SHA-256 (``_hash_email`` below, twin of the Bronze helper) and only the hash
travels, as ``Actor.email_hash``; the raw name survives only as
``display_name``, blanked when it is itself an address (#132). An author no
channel identifies at all is absent, ``Commit.author=None`` — the same
treatment null event actors get, not an ``Actor`` with a blank field.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from coops.domain import (
    PROVIDER_GITHUB,
    ActivityEvent,
    Actor,
    Commit,
    FileEntry,
    FileTree,
    Issue,
    Member,
    ProviderAccount,
    PullRequest,
    Repository,
    TenantId,
    display_name_of,
    identity_key,
)


def _require_github(account: ProviderAccount) -> ProviderAccount:
    """Reject an account from another provider before any mapping happens.

    Mapping a payload this module fetched from GitHub under, say, the
    GitLab account of the same tenant would label GitHub-shaped data with
    the wrong provider — and two providers' records for one tenant are
    exactly what #92 keeps apart. A wiring bug, so ``ValueError`` like
    every address guard in the domain.
    """
    if account.provider != PROVIDER_GITHUB:
        raise ValueError(
            f"the GitHub mapper maps GitHub records, not {account.provider!r}"
        )
    return account


def _hash_email(email: str) -> str:
    """SHA-256 of a trimmed, lower-cased address.

    Twin of ``coops.bronze.commits._hash_email``: same input normalisation,
    so an identity derived here matches one derived by the Bronze scrub.
    Pseudonymization, not anonymization — the point is that no raw address
    crosses onto a domain model.
    """
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _author_actor(
    *,
    login: str | None = None,
    account_id: int | None = None,
    name: str | None = None,
    email: str | None = None,
) -> Actor | None:
    """Resolve a commit author from the provider's three identity channels.

    The email is hashed here and only the hash is passed on, and — matching
    ``_sanitize_commit`` — the hash is kept only when there is no account
    link: a linked author is keyed by ``login``, an unlinked one (5.1% of
    the corpus) by the hash of an email that is their only identifier.
    ``Actor.resolve`` applies the precedence (login -> email_hash -> name)
    and blanks an address-shaped name.

    An author no channel identifies — no login, no account, no name, no
    email (measured 2026-09-23: 1,038 of 28,244 commits in local-run,
    0 of 130,186 in fga-eps-mds, #154: a deleted account,
    or author metadata that never resolved) — is **absent**: ``None``,
    exactly how ``_conversation_actor`` treats a null event actor, never
    an ``Actor`` carrying a blank field (a shared empty identity would
    merge distinct people, #151). ``Actor.resolve`` itself keeps raising
    on an empty identity; this caller decides absence *before* calling it,
    reusing the domain's own ``identity_key`` so there is no second
    notion of "identifies nobody".
    """
    # Hash the email for every author, linked or not — the twin of the same
    # change in ``_sanitize_commit`` (#101/#171). Gating it on ``unlinked``
    # here would discard the hash Bronze now stores, so the join would exist
    # on disk and not in the domain.
    #
    # This does not change any identity: ``identity_key`` resolves
    # ``login -> email_hash -> name``, so a linked author still keys on its
    # login. The hash becomes an additional, non-deciding channel — which is
    # exactly what linking a hash to a login requires.
    email_hash = _hash_email(email) if email else None
    if identity_key(login, email_hash, display_name_of(name)) is None:
        return None
    return Actor.resolve(
        login=login,
        account_id=account_id,
        name=name,
        email_hash=email_hash,
    )


def _conversation_actor(raw_user: Any) -> Actor | None:
    """A user object on an issue/PR/event payload, or ``None``.

    ``None`` (JSON ``null``, a deleted account) stays ``None``: an absent
    actor is not a person named "unknown" — measured, 957 event actors in
    the corpus are literally ``null``.
    """
    if not isinstance(raw_user, Mapping) or not raw_user.get("login"):
        return None
    return Actor.resolve(
        login=raw_user.get("login"),
        account_id=raw_user.get("id"),
    )


def map_repository(
    raw: Mapping[str, Any],
    tenant_id: TenantId,
    account: ProviderAccount,
) -> Repository:
    """Map a REST repository object (``/orgs/{org}/repos`` item).

    ``external_id`` is the provider's numeric repository id, stringified —
    the shape :mod:`coops.domain.models` fixes for the storage key (#39).
    """
    _require_github(account)
    return Repository(
        tenant_id=tenant_id,
        account=account,
        external_id=str(raw["id"]) if raw.get("id") else "",
        name=raw.get("name") or "",
        full_name=raw.get("full_name") or "",
        is_private=bool(raw.get("private")),
        is_fork=bool(raw.get("fork")),
        is_archived=bool(raw.get("archived")),
        description=raw.get("description"),
        default_branch=raw.get("default_branch"),
        language=raw.get("language"),
        html_url=raw.get("html_url"),
        size_kb=raw.get("size"),
        stargazers_count=raw.get("stargazers_count"),
        forks_count=raw.get("forks_count"),
        open_issues_count=raw.get("open_issues_count"),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        pushed_at=raw.get("pushed_at"),
    )


def map_member(
    raw: Mapping[str, Any],
    tenant_id: TenantId,
    account: ProviderAccount,
    *,
    is_org_member: bool = False,
) -> Member:
    """Map a REST user payload to a Member.

    Accepts the shapes that make up the member population, all of which
    carry ``login`` and ``id``: the organization member list item, the
    contributor list item (whose ``contributions`` total is picked up here)
    and the ``/users/{login}`` profile (the only one carrying ``name`` —
    list items yield ``display_name=None``, which is normal). Whether the
    person is an organization member is caller knowledge (which endpoint
    produced the payload), hence the keyword-only flag.

    ``external_id`` is the provider's account id, stringified. ``person_id``
    is deliberately **not** set: it is reserved for cross-provider linking
    (#89's replacement) and the GitHub mapper must not invent a value.
    """
    _require_github(account)
    login = raw.get("login") or None
    display_name = display_name_of(raw.get("name"))
    identity = identity_key(login, None, display_name)
    if identity is None:
        raise ValueError(
            "member payload carries no login and no legible name;"
            " nothing to key a Member on"
        )
    return Member(
        tenant_id=tenant_id,
        account=account,
        identity=identity,
        display_name=display_name,
        external_id=str(raw["id"]) if raw.get("id") else None,
        login=login,
        is_org_member=is_org_member,
        contributions_total=raw.get("contributions") or 0,
    )


def map_commit_graphql(
    node: Mapping[str, Any],
    tenant_id: TenantId,
    account: ProviderAccount,
    repo_name: str,
) -> Commit:
    """Map one GraphQL ``history`` node to a Commit.

    The node shape is what ``graphql_commit_history`` requests (see module
    docstring); ``additions``/``deletions`` are always requested but kept
    optional so a degraded node still maps. ``external_id`` is the ``oid``:
    for a commit the provider's own id *is* the git object id, so it equals
    ``sha`` by GitHub's nature, not by this mapper's choice (see
    :class:`coops.domain.models.Commit`).
    """
    _require_github(account)
    author = node.get("author") or {}
    user = author.get("user") or {}
    parent_nodes = (node.get("parents") or {}).get("nodes") or []
    return Commit(
        tenant_id=tenant_id,
        account=account,
        external_id=node.get("oid") or "",
        repo_name=repo_name,
        sha=node.get("oid") or "",
        author=_author_actor(
            login=user.get("login"),
            account_id=user.get("databaseId"),
            name=author.get("name"),
            email=author.get("email"),
        ),
        committed_at=node.get("committedDate") or "",
        message=node.get("message") or "",
        parents=tuple(
            p["oid"] for p in parent_nodes if isinstance(p, Mapping) and p.get("oid")
        ),
        additions=node.get("additions"),
        deletions=node.get("deletions"),
    )


def map_commit_rest(
    raw: Mapping[str, Any],
    tenant_id: TenantId,
    account: ProviderAccount,
    repo_name: str,
) -> Commit:
    """Map one REST commit object (list item or detail) to a Commit.

    The account link lives in the top-level ``author`` (``null`` for the
    5.1% of authors with no GitHub account) — or, in the Bronze records
    ``_sanitize_commit`` writes, inside ``commit.author`` next to the git
    identity, with no top-level ``author`` at all (measured, #168: 0 of
    28,244 local-run and 0 of 130,186 fga records carry a top-level
    login; 27,206 and 123,562 carry one inside). The top level wins when
    both are present — the same precedence ``_sanitize_commit`` itself
    applies when writing (``login or commit_author.get('login')``).
    ``stats`` is only present on the detail payload, so additions/deletions
    are ``None`` for list items.

    ``committed_at`` is the committer date, falling back to the author's
    date when the record carries no ``commit.committer`` at all — the
    pre-#128 Bronze shape (measured, #168: 100% of one corpus's records),
    which Bronze itself writes the same way (``author.get('date') or
    committed_date`` in :mod:`coops.bronze.commits`). The model keeps
    raising when neither date exists: a commit with no timestamp at all
    is unmappable, not mappable-with-empty.

    ``external_id`` is the commit ``sha`` — for commits the provider's id
    and the git object id are the same string (see
    :class:`coops.domain.models.Commit`).
    """
    _require_github(account)
    top_author = raw.get("author") or {}
    git_author = (raw.get("commit") or {}).get("author") or {}
    git_committer = (raw.get("commit") or {}).get("committer") or {}
    stats = raw.get("stats") or {}
    login = top_author.get("login") or git_author.get("login")
    account_id = top_author.get("id")
    if account_id is None:
        account_id = git_author.get("id")
    return Commit(
        tenant_id=tenant_id,
        account=account,
        external_id=raw.get("sha") or "",
        repo_name=repo_name,
        sha=raw.get("sha") or "",
        author=_author_actor(
            login=login,
            account_id=account_id,
            name=git_author.get("name"),
            email=git_author.get("email"),
        ),
        committed_at=git_committer.get("date") or git_author.get("date") or "",
        message=(raw.get("commit") or {}).get("message") or "",
        authored_at=git_author.get("date"),
        parents=tuple(
            p["sha"]
            for p in raw.get("parents") or []
            if isinstance(p, Mapping) and p.get("sha")
        ),
        additions=stats.get("additions"),
        deletions=stats.get("deletions"),
    )


def map_issue(
    raw: Mapping[str, Any],
    tenant_id: TenantId,
    account: ProviderAccount,
    repo_name: str,
) -> Issue:
    """Map a REST issue payload (no ``pull_request`` object) to an Issue.

    Built from named fields only: ``body`` and ``milestone`` are never read,
    so they cannot reach the model whatever the provider sends.
    ``external_id`` is the provider's own issue id (``id``, not ``number``:
    numbers are per-repository sequences, ids are the provider's key).
    """
    _require_github(account)
    if raw.get("pull_request"):
        raise ValueError(
            f"payload #{raw.get('number')} is a pull request; use map_pull_request"
        )
    return Issue(
        tenant_id=tenant_id,
        account=account,
        external_id=str(raw["id"]) if raw.get("id") else "",
        repo_name=repo_name,
        number=raw.get("number") or 0,
        state=raw.get("state") or "",
        title=raw.get("title") or "",
        author=_conversation_actor(raw.get("user")),
        assignee=_conversation_actor(raw.get("assignee")),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        closed_at=raw.get("closed_at"),
    )


def map_pull_request(
    raw: Mapping[str, Any],
    tenant_id: TenantId,
    account: ProviderAccount,
    repo_name: str,
) -> PullRequest:
    """Map a REST issue payload that carries a ``pull_request`` object."""
    _require_github(account)
    pr = raw.get("pull_request")
    if not pr:
        raise ValueError(
            f"payload #{raw.get('number')} has no pull_request object; use map_issue"
        )
    return PullRequest(
        tenant_id=tenant_id,
        account=account,
        external_id=str(raw["id"]) if raw.get("id") else "",
        repo_name=repo_name,
        number=raw.get("number") or 0,
        state=raw.get("state") or "",
        title=raw.get("title") or "",
        author=_conversation_actor(raw.get("user")),
        assignee=_conversation_actor(raw.get("assignee")),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
        closed_at=raw.get("closed_at"),
        merged_at=pr.get("merged_at"),
        draft=bool(raw.get("draft")),
    )


def map_activity_event(
    raw: Mapping[str, Any],
    tenant_id: TenantId,
    account: ProviderAccount,
    repo_name: str,
) -> ActivityEvent:
    """Map a REST issue-event payload to an ActivityEvent."""
    _require_github(account)
    issue = raw.get("issue")
    return ActivityEvent(
        tenant_id=tenant_id,
        account=account,
        external_id=str(raw["id"]) if raw.get("id") else "",
        repo_name=repo_name,
        event_type=raw.get("event") or "",
        created_at=raw.get("created_at") or "",
        actor=_conversation_actor(raw.get("actor")),
        issue_number=issue.get("number") if isinstance(issue, Mapping) else None,
    )


def map_file_tree_rest(
    raw: Mapping[str, Any],
    tenant_id: TenantId,
    account: ProviderAccount,
    repo_name: str,
    branch: str | None = None,
) -> FileTree:
    """Map a REST Git Trees response (``?recursive=1``) to a FileTree.

    Refuses a response with ``truncated: true``: a partial tree mapped as a
    whole one is silent data loss, and the extraction layer falls back to
    GraphQL precisely so that never reaches storage. ``external_id`` is the
    response's ``sha`` — the tree's own id.
    """
    _require_github(account)
    if raw.get("truncated"):
        raise ValueError("refusing to map a truncated REST tree; fall back to GraphQL")
    entries = tuple(
        FileEntry(
            path=item.get("path") or "",
            kind=item.get("type") or "",
            sha=item.get("sha"),
            mode=item.get("mode"),
            size=item.get("size"),
        )
        for item in raw.get("tree") or []
        if isinstance(item, Mapping)
    )
    return FileTree(
        tenant_id=tenant_id,
        account=account,
        repo_name=repo_name,
        branch=branch,
        sha=raw.get("sha"),
        external_id=raw.get("sha"),
        entries=entries,
    )


def map_file_tree_graphql(
    entries: Any,
    tenant_id: TenantId,
    account: ProviderAccount,
    repo_name: str,
    branch: str | None = None,
) -> FileTree:
    """Map one level of GraphQL ``... on Tree { entries { ... } }``.

    The GraphQL walk fetches each directory with its own query, and
    ``TreeEntry.path`` is the full path from the repository root, so every
    level maps independently and the caller composes them. Blob facts
    (``oid``, ``byteSize``, ``isBinary``) sit under ``object`` and exist for
    blobs only, so a directory entry maps with ``sha``/``size``/``is_binary``
    all ``None``. There is no tree sha at this level, so ``external_id`` is
    ``None`` too: the level has no id of its own.
    """
    _require_github(account)
    mapped = []
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        blob = entry.get("object") or {}
        mapped.append(
            FileEntry(
                path=entry.get("path") or "",
                kind=entry.get("type") or "",
                sha=blob.get("oid"),
                mode=entry.get("mode"),
                size=blob.get("byteSize"),
                is_binary=blob.get("isBinary"),
            )
        )
    return FileTree(
        tenant_id=tenant_id,
        account=account,
        repo_name=repo_name,
        branch=branch,
        entries=tuple(mapped),
    )
