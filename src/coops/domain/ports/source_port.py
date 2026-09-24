"""The source port: tenant-scoped reads of a provider's records, as models.

This is the port the extraction layer moves onto once #26 (Bronze) lands;
until then nothing consumes it. Where :class:`StoragePort` generalises
``RawStore`` over *persistence*, this one generalises what
``coops.utils.github_api.GitHubAPIClient`` does over *extraction*: it turns
"ask the provider about an organization" into six reads that return domain
models (:mod:`coops.domain.models`), built by a mapper
(:mod:`coops.github.mapper`) that lives with the adapter — never on this
side of the boundary. When the GitLab adapter arrives (#49),
``application/``, ``silver/`` and ``gold/`` must need zero changes, which
is the whole point of the port.

The guarantees carried over from ``RawStore`` and ``StoragePort``, because
they are proven:

- **A `TenantId` is the first, required parameter of every method**, and the
  implementation scopes every read to it. There is no method that addresses
  records without one, so a caller cannot read another tenant's records by
  omitting a filter.
- **Structural** (`typing.Protocol`), not an ABC: an implementation is any
  object with these methods.
- **The port owns its error vocabulary.** A provider condition crosses this
  boundary as one of the `SourceError` types below, never as the provider
  library's own exception — an adapter that lets ``requests.HTTPError``
  out has leaked itself exactly as an ``etag`` parameter would.

Provider-neutral by construction, so nothing GitHub-shaped is expressible
here: no ``etag``, no ``per_page``, no ``Link`` header, no REST-vs-GraphQL
choice, no pagination at all. Those are adapter concerns; a caller that can
tell which provider answered has been handed a leak.

Decisions the issue text leaves open, made here so #26 does not have to
break them:

- **`Iterator`, not `list`.** Measured, one real organization holds 130,186
  commits; a method returning a list commits every adapter — including the
  GitLab one (#49) and any replay-from-corpus adapter — to materialising
  the whole corpus in memory at once. An iterator lets an adapter stream
  page by page, and one pass is all a caller needs (records flow into
  storage as they arrive). A caller that wants a list writes ``list(...)``.
  Consequence: a lazy adapter raises provider conditions during iteration
  rather than at the call — both are within contract, so callers must
  consume the iterator to see errors.
- **Incrementality is adapter-internal; there is no ``since`` parameter.**
  The per-repository watermarks (#110) are heterogeneous: a ``since``
  timestamp *per branch* (commits), a numeric last-seen event id (events —
  a GitHub concept GitLab does not share), a ``last_updated_at`` bound
  (issues/PRs) and remembered head SHAs (trees — not a time at all). One
  neutral ``since`` cannot express that set; a set of cursor parameters
  shaped like GitHub's would leak the provider this port exists to hide.
  Watermarks change *which requests a run makes*, not what a fetch means:
  the contract here is "the tenant's records", and the merge semantics that
  make an incremental run equal a full one stay with the caller (Bronze's
  merge-by-number, commit dedup). If #26 ever needs a caller-driven window,
  it is an additive defaulted keyword; re-shaping a wrong ``since`` then
  would be the breaking change this decision exists to avoid.
- **`fetch_tree` is addressed by ``(tenant, repository, branch)``, with
  ``branch=None`` meaning the repository's default branch** — a neutral
  concept every provider has, unlike a commit SHA, which is a watermark
  concern (the adapter skips an unchanged tree by its remembered head SHA,
  invisible here). It returns one `FileTree`, not an iterator: a tree is a
  single snapshot, and the model already carries its entries as a tuple.
- **Repositories are addressed by their bare name within the tenant**
  (``"2016.1-Partiu_backend"``, the ``repo_name`` every domain model
  carries). The tenant *is* the owner, so the bare name identifies the
  repository; :func:`validate_repo_name` rejects path separators so the
  provider's ``owner/repo`` string cannot be used as an address. The model
  keeps its ``full_name`` field as *data* (#21); nothing addresses through
  it. A branch name, by contrast, may contain ``/`` — real git ref names
  do (``release/2026.1``) — and :func:`validate_branch` only rejects blank.

Validation follows the domain's own convention: a malformed *address* is a
caller bug and raises `ValueError` (as every model's ``__post_init__``
does), while a *provider* condition — deleted repository, revoked token,
rate limit — raises `SourceError`. The distinction matters to the caller:
one is fixed in the caller's code, the other in the world.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from coops.domain.models import Commit, FileTree, Issue, Member, PullRequest, Repository
from coops.domain.tenancy import TenantId


def validate_repo_name(repo_name: str) -> str:
    """Return the trimmed repository name, or raise ``ValueError``.

    Implementations call this on every repository-addressed fetch so the
    rules hold regardless of the caller:

    - non-empty after trimming (the name is the whole address within the
      tenant — there is nothing else to key on);
    - no path separators: the tenant is the owner, so the bare name
      identifies the repository, and an ``owner/repo`` form would smuggle
      the provider's addressing scheme through this port (and could address
      a repository the tenant does not own).
    """
    name = (repo_name or "").strip()
    if not name:
        raise ValueError("repository name must be a non-empty string")
    if "/" in name or "\\" in name:
        raise ValueError(
            f"repository name {name!r} must not contain a path separator:"
            " address repositories by their bare name within the tenant,"
            " never the provider's owner/repo form"
        )
    return name


def validate_branch(branch: str) -> str:
    """Return the trimmed branch name, or raise ``ValueError``.

    Unlike a repository name, a branch name may contain ``/`` — git ref
    names do (``release/2026.1``) — so only blank is rejected. ``None`` is
    not invalid at all: it selects the repository's default branch and
    never reaches this validator.
    """
    name = (branch or "").strip()
    if not name:
        raise ValueError(
            "branch name must be a non-empty string (None selects the default branch)"
        )
    return name


class SourceError(Exception):
    """A provider condition, in the port's vocabulary.

    The base class every error crossing this boundary derives from. An
    adapter must translate its library's exceptions into these types —
    provider detail belongs in the adapter's logs, not in the exception a
    caller sees, because a message like ``403 Client Error for
    api.github.com`` names the provider exactly as an ``etag`` parameter
    would.
    """


class SourceUnavailableError(SourceError):
    """The provider could not serve the read *right now*.

    Rate limits, server errors, network faults. Retrying later is
    reasonable; nothing about the tenant's data or credentials is wrong.
    """


class SourceAccessError(SourceError):
    """The tenant's credentials do not permit this read.

    A revoked token, a missing scope, a private repository this token
    cannot see. Not retryable: an operator has to act.
    """


class SourceNotFoundError(SourceError):
    """The addressed repository or branch does not exist for this tenant.

    A deleted (or never-existing, or renamed-away) repository, or a branch
    that is not there. Raised loudly instead of returning an empty
    iterator on purpose: "no records" is a legitimate answer a dashboard
    must be able to show, and an empty result for a repository that has
    vanished would present exactly that — absence is the dangerous answer
    (``docs/definition-of-done.md``).
    """


@runtime_checkable
class SourcePort(Protocol):
    """Tenant-scoped reads of a provider's records, as domain models.

    Every method requires a `TenantId` as its first parameter and the
    implementation scopes every read to it, so a caller cannot read
    another tenant's records by omitting a filter — there is no method
    that addresses records without one.

    The iterator-returning methods are lazy by permission, not by
    requirement: an implementation may validate eagerly and raise before
    returning, or stream and raise during iteration. Both are within
    contract, so callers consume the iterator rather than trusting the
    call itself to have succeeded.
    """

    def fetch_repositories(self, tenant: TenantId) -> Iterator[Repository]:
        """Yield the tenant organization's repositories.

        Organization-wide: no repository address. Every record is a
        `Repository` — never a provider payload, so nothing downstream
        can grow a dependency on a provider field.
        """
        ...

    def fetch_members(self, tenant: TenantId) -> Iterator[Member]:
        """Yield the tenant's member/contributor population.

        The union the `Member` model describes: organization members and
        everyone who contributed to the tenant's repositories. Whether a
        population is assembled from one provider call or several is the
        adapter's business.
        """
        ...

    def fetch_commits(
        self,
        tenant: TenantId,
        repo_name: str,
    ) -> Iterator[Commit]:
        """Yield the repository's commits, oldest to newest when ordered.

        Raises `SourceNotFoundError` when the repository does not exist
        for this tenant, `ValueError` for an invalid name. Which branches
        count as "the repository's commits" is a provider policy the
        adapter applies; the union-with-dedup that
        ``graphql_commit_history`` performs today is exactly that.
        """
        ...

    def fetch_issues(
        self,
        tenant: TenantId,
        repo_name: str,
    ) -> Iterator[Issue]:
        """Yield the repository's issues — conversations that are not PRs.

        The split between issues and pull requests is the model's
        (`Issue` and `PullRequest` are distinct types), so a provider that
        serves them from one endpoint splits them behind this port rather
        than in front of it.
        """
        ...

    def fetch_pull_requests(
        self,
        tenant: TenantId,
        repo_name: str,
    ) -> Iterator[PullRequest]:
        """Yield the repository's pull requests.

        The issue/PR split noted on `fetch_issues` applies in both
        directions: a payload the provider marks as a pull request must
        not arrive here as an `Issue`.
        """
        ...

    def fetch_tree(
        self,
        tenant: TenantId,
        repo_name: str,
        branch: str | None = None,
    ) -> FileTree:
        """Return the repository's file tree on one branch.

        ``branch=None`` selects the repository's default branch — a
        concept every provider has. Returns a single `FileTree` (a tree
        is a snapshot, not a stream) whose entries are complete by
        construction: a provider response that would arrive truncated is
        the adapter's problem to finish (the REST/GraphQL fallback), never
        the caller's. Raises `SourceNotFoundError` for an unknown
        repository or branch.
        """
        ...
