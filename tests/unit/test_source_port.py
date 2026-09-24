"""Contract tests for coops.domain.ports.source_port — the source port.

A `Protocol` ships no behaviour of its own, so four things are tested here:

1. **The interface shape** — every method takes the `TenantId` first and
   positionally. That structural fact *is* the tenant guard: it is what
   leaves "no method that addresses records without one" true.
2. **The address validators** — the guards that make the provider's
   ``owner/repo`` addressing form unrepresentable through this port.
3. **The error vocabulary** — provider conditions cross as `SourceError`
   subclasses, never as the provider library's own exceptions.
4. **The contract** every implementation must hold, run against a
   reference in-memory implementation (`InMemorySource`). The GitHub
   adapter that lands with #26 and the GitLab one (#49) will be held to
   the same contract by subclassing `SourcePortContract`.

Names in these tests are synthetic (semester 2099 does not exist; the
logins are invented, as in ``test_github_mapper.py``). They mirror the
shapes on disk — repository names with dots and underscores, branch names
with slashes — because a port that cannot express those shapes is wrong.
The real corpus is driven separately, read-only, by
``scripts/source_port_corpus_check.py``.
"""

from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from coops.domain import (
    Actor,
    Commit,
    FileEntry,
    FileTree,
    Issue,
    Member,
    PROVIDER_GITHUB,
    ProviderAccount,
    PullRequest,
    Repository,
    TenantId,
)
from coops.domain.ports import (
    SourceAccessError,
    SourceError,
    SourceNotFoundError,
    SourcePort,
    SourceUnavailableError,
    validate_branch,
    validate_repo_name,
)

PORT_METHODS = (
    "fetch_repositories",
    "fetch_members",
    "fetch_commits",
    "fetch_issues",
    "fetch_pull_requests",
    "fetch_tree",
)

#: The per-repository kinds, for tests that run the same assertion over
#: every repository-addressed read.
REPO_KINDS = ("commits", "issues", "pull_requests", "tree")


class InMemorySource:
    """Reference `SourcePort`: per-tenant dicts of already-built models.

    The tenant is part of every storage key — injected here, never read
    from the data — which is where the isolation the port promises is
    actually enforced. Address validators run on every repository-addressed
    read, and a repository that is not there raises `SourceNotFoundError`
    rather than answering with an empty iterator.

    ``unavailable`` is the outage injection point: when set, the simulated
    provider raises it (a native library exception), and the read path
    must translate it into the port's vocabulary before it can reach a
    caller.
    """

    def __init__(self) -> None:
        # tenant_id -> {"repositories": [...], "members": [...],
        #               "repos": {name: {...}}}
        self._state: dict[str, dict] = {}
        self.unavailable: Exception | None = None

    # -- test-side seeding (not part of the port) ------------------------

    def _tenant(self, tenant: TenantId) -> dict:
        tenant_id = str(tenant)
        if tenant_id not in self._state:
            self._state[tenant_id] = {
                "repositories": [],
                "members": [],
                "repos": {},
            }
        return self._state[tenant_id]

    def add_repository(self, tenant: TenantId, repository: Repository) -> None:
        self._tenant(tenant)["repositories"].append(repository)

    def add_member(self, tenant: TenantId, member: Member) -> None:
        self._tenant(tenant)["members"].append(member)

    def add_repository_records(
        self,
        tenant: TenantId,
        repo_name: str,
        *,
        default_branch: str,
        commits: tuple[Commit, ...] = (),
        issues: tuple[Issue, ...] = (),
        pull_requests: tuple[PullRequest, ...] = (),
        trees: dict[str, FileTree] | None = None,
    ) -> None:
        repo = self._tenant(tenant)["repos"].setdefault(
            repo_name,
            {
                "default_branch": default_branch,
                "commits": [],
                "issues": [],
                "pull_requests": [],
                "trees": {},
            },
        )
        repo["commits"].extend(commits)
        repo["issues"].extend(issues)
        repo["pull_requests"].extend(pull_requests)
        repo["trees"].update(trees or {})

    # -- the read path ---------------------------------------------------

    def _provider_call(self, read):
        """Run one read against the simulated provider.

        A native provider-library exception must never cross the port:
        it is translated into `SourceUnavailableError`. The port's own
        errors (``SourceError``) pass through untranslated — translating
        a `SourceNotFoundError` into "unavailable" would turn a deleted
        repository into a transient fault.
        """
        try:
            if self.unavailable is not None:
                raise self.unavailable
            return read()
        except SourceError:
            raise
        except Exception as exc:
            raise SourceUnavailableError(
                "the provider could not serve the read right now"
            ) from exc

    def _repo(self, tenant: TenantId, repo_name: str) -> dict:
        repos = self._tenant(tenant)["repos"]
        if repo_name not in repos:
            raise SourceNotFoundError(f"no repository {repo_name!r} for this tenant")
        return repos[repo_name]

    def fetch_repositories(self, tenant: TenantId):
        return self._provider_call(
            lambda: iter(list(self._tenant(tenant)["repositories"]))
        )

    def fetch_members(self, tenant: TenantId):
        return self._provider_call(lambda: iter(list(self._tenant(tenant)["members"])))

    def fetch_commits(self, tenant: TenantId, repo_name: str):
        name = validate_repo_name(repo_name)
        return self._provider_call(
            lambda: iter(list(self._repo(tenant, name)["commits"]))
        )

    def fetch_issues(self, tenant: TenantId, repo_name: str):
        name = validate_repo_name(repo_name)
        return self._provider_call(
            lambda: iter(list(self._repo(tenant, name)["issues"]))
        )

    def fetch_pull_requests(self, tenant: TenantId, repo_name: str):
        name = validate_repo_name(repo_name)
        return self._provider_call(
            lambda: iter(list(self._repo(tenant, name)["pull_requests"]))
        )

    def fetch_tree(self, tenant: TenantId, repo_name: str, branch: str | None = None):
        # Address validation is caller-bug territory and runs before any
        # provider interaction, so a ValueError can never surface as a
        # translated provider condition.
        name = validate_repo_name(repo_name)
        selected: str | None = None if branch is None else validate_branch(branch)

        def read() -> FileTree:
            repo = self._repo(tenant, name)
            branch_name = repo["default_branch"] if selected is None else selected
            trees = repo["trees"]
            if branch_name not in trees:
                raise SourceNotFoundError(
                    f"no branch {branch_name!r} on repository {name!r}"
                )
            return trees[branch_name]

        return self._provider_call(read)


def fetch_records(
    source: SourcePort,
    kind: str,
    tenant: TenantId,
    repo_name: str = "demo-api",
    branch: str | None = None,
):
    """Drain one fetch completely, so a lazy adapter's errors surface."""
    if kind == "commits":
        return list(source.fetch_commits(tenant, repo_name))
    if kind == "issues":
        return list(source.fetch_issues(tenant, repo_name))
    if kind == "pull_requests":
        return list(source.fetch_pull_requests(tenant, repo_name))
    if kind == "tree":
        return [source.fetch_tree(tenant, repo_name, branch)]
    raise ValueError(f"unknown kind {kind!r}")


class TestPortShape:
    """The tenant guard is the signature: assert it, so it cannot be edited away."""

    @staticmethod
    def _parameters(method_name: str) -> dict[str, inspect.Parameter]:
        parameters = inspect.signature(getattr(SourcePort, method_name)).parameters
        return {name: p for name, p in parameters.items() if name != "self"}

    @pytest.mark.parametrize("method_name", PORT_METHODS)
    def test_tenant_is_the_first_parameter(self, method_name):
        assert next(iter(self._parameters(method_name))) == "tenant"

    @pytest.mark.parametrize("method_name", PORT_METHODS)
    def test_tenant_is_required_not_defaulted(self, method_name):
        parameters = self._parameters(method_name)
        assert parameters["tenant"].default is inspect.Parameter.empty

    @pytest.mark.parametrize("method_name", PORT_METHODS)
    def test_tenant_parameter_is_the_domain_type(self, method_name):
        hints = get_type_hints(getattr(SourcePort, method_name))
        assert hints["tenant"] is TenantId

    def test_reference_fake_satisfies_the_protocol(self):
        assert isinstance(InMemorySource(), SourcePort)


class TestValidateRepoName:
    @pytest.mark.parametrize(
        "repo_name",
        [
            # Shapes found on disk, with invented values: bare names with
            # dots, underscores and dashes.
            "2099.1-Demo.App",
            "2098.2-Demo_backend",
            "Demo",
            "a-disciplina-2099",
        ],
    )
    def test_accepts_every_repository_name_shape_on_disk_today(self, repo_name):
        assert validate_repo_name(repo_name) == repo_name

    def test_trims_surrounding_whitespace(self):
        assert validate_repo_name("  2099.1-Demo \t") == "2099.1-Demo"

    @pytest.mark.parametrize("repo_name", ["", "   ", "\t\n"])
    def test_rejects_blank_names(self, repo_name):
        with pytest.raises(ValueError, match="non-empty"):
            validate_repo_name(repo_name)

    @pytest.mark.parametrize("repo_name", ["org-a/2099.1-Demo", "org-a\\2099.1-Demo"])
    def test_rejects_the_provider_owner_slash_repo_form(self, repo_name):
        # The tenant is the owner; an owner/repo string is the provider's
        # own addressing scheme (and could address a repository outside
        # the tenant). Rejecting the separator makes it unrepresentable.
        with pytest.raises(ValueError, match="path separator"):
            validate_repo_name(repo_name)


class TestValidateBranch:
    @pytest.mark.parametrize(
        "branch",
        ["main", "master", "devel", "release/2026.1", "feature/demo-api"],
    )
    def test_accepts_git_ref_names_including_slashes(self, branch):
        # Unlike a repository name, a branch name may contain "/": real
        # git ref names do, and rejecting them would leave genuine
        # branches unaddressable.
        assert validate_branch(branch) == branch

    def test_trims_surrounding_whitespace(self):
        assert validate_branch("  release/2026.1 ") == "release/2026.1"

    @pytest.mark.parametrize("branch", ["", "   "])
    def test_rejects_blank_names(self, branch):
        with pytest.raises(ValueError, match="non-empty"):
            validate_branch(branch)


class TestSourceErrors:
    def test_provider_conditions_share_one_base(self):
        for error in (SourceUnavailableError, SourceAccessError, SourceNotFoundError):
            assert issubclass(error, SourceError)

    def test_provider_conditions_are_not_caller_bugs(self):
        # Two channels, deliberately distinct: ValueError is the caller's
        # mistake (a malformed address), SourceError the world's state.
        # If one became the other, callers would catch the wrong class.
        assert not issubclass(SourceError, ValueError)
        assert not issubclass(ValueError, SourceError)


class SourcePortContract:
    """Behaviours every `SourcePort` implementation must hold.

    Subclass and implement `make_source`, then seed it with the fixture
    builders. The tenant-isolation tests are the point of the port: a
    source holding two tenants' data must never return tenant B's records
    for a tenant A call, on any method.
    """

    def make_source(self) -> InMemorySource:
        raise NotImplementedError

    # -- fixture builders (synthetic values only) ------------------------

    TENANT_A = TenantId("org-a")
    TENANT_B = TenantId("org-b")
    ACCOUNT_A = ProviderAccount(PROVIDER_GITHUB, "org-a")
    ACCOUNT_B = ProviderAccount(PROVIDER_GITHUB, "org-b")

    @classmethod
    def _commit(cls, tenant: TenantId, repo_name: str, sha: str) -> Commit:
        return Commit(
            tenant_id=tenant,
            account=cls._account_of(tenant),
            external_id=sha,
            repo_name=repo_name,
            sha=sha,
            author=Actor.resolve(login="rosa-almeida", account_id=1001),
            committed_at="2026-03-04T10:00:00Z",
            message="Refactor the capture loop",
        )

    @classmethod
    def _account_of(cls, tenant: TenantId) -> ProviderAccount:
        return cls.ACCOUNT_A if tenant == cls.TENANT_A else cls.ACCOUNT_B

    @classmethod
    def _seeded(cls, source: InMemorySource) -> InMemorySource:
        """Two tenants, colliding on one repository name on purpose."""
        a, b = cls.TENANT_A, cls.TENANT_B
        source.add_repository(
            a,
            Repository(
                tenant_id=a,
                account=cls.ACCOUNT_A,
                external_id="101",
                name="demo-api",
                full_name="org-a/demo-api",
            ),
        )
        source.add_repository(
            a,
            Repository(
                tenant_id=a,
                account=cls.ACCOUNT_A,
                external_id="102",
                name="demo-web",
                full_name="org-a/demo-web",
            ),
        )
        source.add_repository(
            b,
            Repository(
                tenant_id=b,
                account=cls.ACCOUNT_B,
                external_id="201",
                name="demo-api",
                full_name="org-b/demo-api",
            ),
        )
        source.add_member(
            a,
            Member(
                tenant_id=a,
                account=cls.ACCOUNT_A,
                identity="rosa-almeida",
                display_name=None,
                login="rosa-almeida",
                external_id="1001",
            ),
        )
        source.add_member(
            b,
            Member(
                tenant_id=b,
                account=cls.ACCOUNT_B,
                identity="joao-silva",
                display_name=None,
                login="joao-silva",
                external_id="2001",
            ),
        )
        main_tree = FileTree(
            tenant_id=a,
            account=cls.ACCOUNT_A,
            repo_name="demo-api",
            branch="main",
            entries=(FileEntry(path="README.md", kind="blob"),),
        )
        release_tree = FileTree(
            tenant_id=a,
            account=cls.ACCOUNT_A,
            repo_name="demo-api",
            branch="release/2026.1",
            entries=(FileEntry(path="CHANGELOG.md", kind="blob"),),
        )
        source.add_repository_records(
            a,
            "demo-api",
            default_branch="main",
            commits=(
                cls._commit(a, "demo-api", "a" * 40),
                cls._commit(a, "demo-api", "b" * 40),
            ),
            issues=(
                Issue(
                    tenant_id=a,
                    account=cls.ACCOUNT_A,
                    external_id="9001",
                    repo_name="demo-api",
                    number=1,
                    state="open",
                    title="Fix the parser",
                ),
            ),
            pull_requests=(
                PullRequest(
                    tenant_id=a,
                    account=cls.ACCOUNT_A,
                    external_id="9002",
                    repo_name="demo-api",
                    number=2,
                    state="closed",
                    title="Rewrite the parser",
                    merged_at="2026-03-02T09:00:00Z",
                ),
            ),
            trees={"main": main_tree, "release/2026.1": release_tree},
        )
        source.add_repository_records(
            a,
            "demo-web",
            default_branch="main",
            commits=(cls._commit(a, "demo-web", "c" * 40),),
            issues=(
                Issue(
                    tenant_id=a,
                    account=cls.ACCOUNT_A,
                    external_id="9101",
                    repo_name="demo-web",
                    number=1,
                    state="open",
                    title="Add the parser",
                ),
            ),
            trees={
                "main": FileTree(
                    tenant_id=a,
                    account=cls.ACCOUNT_A,
                    repo_name="demo-web",
                    branch="main",
                    entries=(FileEntry(path="demo-web.md", kind="blob"),),
                ),
            },
        )
        source.add_repository_records(
            b,
            "demo-api",
            default_branch="main",
            commits=(cls._commit(b, "demo-api", "d" * 40),),
            issues=(
                Issue(
                    tenant_id=b,
                    account=cls.ACCOUNT_B,
                    external_id="9201",
                    repo_name="demo-api",
                    number=1,
                    state="open",
                    title="Outro parser",
                ),
            ),
            pull_requests=(
                PullRequest(
                    tenant_id=b,
                    account=cls.ACCOUNT_B,
                    external_id="9202",
                    repo_name="demo-api",
                    number=3,
                    state="open",
                    title="Outro parser ainda",
                ),
            ),
            trees={
                "main": FileTree(
                    tenant_id=b,
                    account=cls.ACCOUNT_B,
                    repo_name="demo-api",
                    branch="main",
                    entries=(FileEntry(path="org-b.md", kind="blob"),),
                ),
            },
        )
        return source

    @pytest.fixture
    def source(self) -> InMemorySource:
        return self._seeded(self.make_source())

    # -- tenant isolation: the point of the port -------------------------

    def test_fetch_repositories_returns_only_the_calling_tenants(self, source):
        repos = list(source.fetch_repositories(self.TENANT_A))
        assert sorted(r.name for r in repos) == ["demo-api", "demo-web"]
        assert all(r.tenant_id == self.TENANT_A for r in repos)
        assert [r.name for r in source.fetch_repositories(self.TENANT_B)] == [
            "demo-api"
        ]

    def test_fetch_members_returns_only_the_calling_tenants(self, source):
        members = list(source.fetch_members(self.TENANT_A))
        assert [m.login for m in members] == ["rosa-almeida"]
        assert [m.login for m in source.fetch_members(self.TENANT_B)] == ["joao-silva"]

    @pytest.mark.parametrize("kind", REPO_KINDS)
    def test_tenant_isolation_on_every_fetch_method(self, source, kind):
        # Both tenants hold data under the SAME repository name. Every
        # record returned to tenant A must be tenant A's — never B's.
        records = fetch_records(source, kind, self.TENANT_A, "demo-api")
        assert records, f"{kind} should have records for the calling tenant"
        assert all(record.tenant_id == self.TENANT_A for record in records)

    def test_same_repo_name_in_two_tenants_is_answered_per_tenant(self, source):
        # The combination, not the members: tenant and repository both
        # distinguish here, and each call must answer from its own side.
        shas_a = {c.sha for c in source.fetch_commits(self.TENANT_A, "demo-api")}
        shas_b = {c.sha for c in source.fetch_commits(self.TENANT_B, "demo-api")}
        assert shas_a == {"a" * 40, "b" * 40}
        assert shas_b == {"d" * 40}
        assert not shas_a & shas_b

    def test_absent_tenant_reads_empty_not_the_other_tenants(self, source):
        # A tenant with nothing stored gets nothing — not tenant B's data
        # by default. Organization-wide reads are empty; a repository
        # address is not-found, which is the loud form of the same rule.
        nobody = TenantId("org-nobody")
        assert list(source.fetch_repositories(nobody)) == []
        assert list(source.fetch_members(nobody)) == []
        with pytest.raises(SourceNotFoundError):
            list(source.fetch_commits(nobody, "demo-api"))

    def test_tenant_slug_spellings_stay_separate_tenants(self, source):
        # Since #92 the slug is an assigned identifier compared exactly; the
        # GitHub case-insensitivity rule lives in ProviderAccount. A second
        # spelling is a different tenant and must not see the first's data.
        stored = list(source.fetch_commits(TenantId("org-a"), "demo-api"))
        assert {c.sha for c in stored} == {"a" * 40, "b" * 40}
        with pytest.raises(SourceNotFoundError):
            list(source.fetch_commits(TenantId("ORG-A"), "demo-api"))

    # -- repository scope -------------------------------------------------

    def test_fetch_is_scoped_to_the_repository(self, source):
        # Two repositories in one tenant, both present: each read answers
        # from its own repository.
        api_shas = {c.sha for c in source.fetch_commits(self.TENANT_A, "demo-api")}
        web_shas = {c.sha for c in source.fetch_commits(self.TENANT_A, "demo-web")}
        assert api_shas == {"a" * 40, "b" * 40}
        assert web_shas == {"c" * 40}
        assert [i.number for i in source.fetch_issues(self.TENANT_A, "demo-web")] == [1]

    @pytest.mark.parametrize("kind", REPO_KINDS)
    def test_unknown_repository_raises_source_not_found(self, source, kind):
        with pytest.raises(SourceNotFoundError):
            fetch_records(source, kind, self.TENANT_A, "no-such-repo")

    def test_blank_repository_name_is_a_caller_bug_not_a_provider_condition(
        self, source
    ):
        # Both conditions present at once: the address is blank AND no
        # such repository exists. The caller bug wins — ValueError, not
        # SourceNotFoundError — because it is fixed in the caller's code.
        with pytest.raises(ValueError, match="non-empty"):
            list(source.fetch_commits(self.TENANT_A, "   "))
        with pytest.raises(ValueError, match="path separator"):
            source.fetch_tree(self.TENANT_A, "org-a/demo-api")

    # -- trees ------------------------------------------------------------

    def test_fetch_tree_returns_the_named_branch(self, source):
        # Two branches present: each call answers from its own branch.
        main = source.fetch_tree(self.TENANT_A, "demo-api", "main")
        release = source.fetch_tree(self.TENANT_A, "demo-api", "release/2026.1")
        assert [e.path for e in main.entries] == ["README.md"]
        assert [e.path for e in release.entries] == ["CHANGELOG.md"]

    def test_fetch_tree_none_branch_selects_the_default_when_both_present(self, source):
        # The precedence: with two branches stored and branch=None, the
        # default branch is the one that answers.
        tree = source.fetch_tree(self.TENANT_A, "demo-api")
        assert tree.branch == "main"
        assert [e.path for e in tree.entries] == ["README.md"]

    def test_unknown_branch_raises_source_not_found(self, source):
        with pytest.raises(SourceNotFoundError):
            source.fetch_tree(self.TENANT_A, "demo-api", "no-such-branch")

    def test_blank_branch_is_a_caller_bug(self, source):
        with pytest.raises(ValueError, match="non-empty"):
            source.fetch_tree(self.TENANT_A, "demo-api", "   ")

    # -- models and errors crossing the port ------------------------------

    @pytest.mark.parametrize(
        ("kind", "model"),
        [
            ("commits", Commit),
            ("issues", Issue),
            ("pull_requests", PullRequest),
        ],
    )
    def test_fetch_returns_domain_models_not_provider_payloads(
        self, source, kind, model
    ):
        for record in fetch_records(source, kind, self.TENANT_A, "demo-api"):
            assert isinstance(record, model)
        for repository in source.fetch_repositories(self.TENANT_A):
            assert isinstance(repository, Repository)
        for member in source.fetch_members(self.TENANT_A):
            assert isinstance(member, Member)
        assert isinstance(source.fetch_tree(self.TENANT_A, "demo-api"), FileTree)

    def test_pull_request_facts_cross_the_port(self, source):
        # merged_at/draft are fields the published corpus cannot exercise
        # (the Bronze projection drops them); this synthetic fixture is
        # what covers them.
        prs = list(source.fetch_pull_requests(self.TENANT_A, "demo-api"))
        assert [pr.merged_at for pr in prs] == ["2026-03-02T09:00:00Z"]
        assert [pr.draft for pr in prs] == [False]

    def test_provider_outage_surfaces_as_the_ports_unavailable_error(self, source):
        # Fail closed on the effect that must NOT happen: the provider
        # library's own exception crossing the port.
        source.unavailable = RuntimeError("connection reset — a provider library error")
        with pytest.raises(SourceUnavailableError) as excinfo:
            list(source.fetch_commits(self.TENANT_A, "demo-api"))
        assert not isinstance(excinfo.value, RuntimeError)

    def test_not_found_stays_not_found_under_translation(self, source):
        # The translation guard must not swallow the port's own errors:
        # a deleted repository stays SourceNotFoundError even though the
        # read runs inside the outage-translating provider call. (No
        # outage is injected here — with one, "unavailable" is the honest
        # answer, since the provider cannot be asked whether the
        # repository exists.)
        with pytest.raises(SourceNotFoundError):
            source.fetch_tree(self.TENANT_A, "no-such-repo", "main")


class TestInMemorySourcePort(SourcePortContract):
    def make_source(self) -> InMemorySource:
        return InMemorySource()
