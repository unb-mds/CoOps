"""Contract tests for coops.domain.ports.ai_port — the AI summary port.

A `Protocol` ships no behaviour of its own, so four things are tested here:

1. **The interface shape** — the method takes the `TenantId` first and
   positionally, and nothing else about the provider leaked into the module:
   a source sweep (with a control proving it can find a leak) enforces the
   "caller cannot tell the provider" rule.
2. **The validators and value types** — the guards that make an invalid
   request and an inconsistent outcome unrepresentable.
3. **The contract** every implementation must hold, run against a reference
   in-memory implementation (`InMemoryAiSummary`). The adapter that replaces
   the incumbent provider call will be held to the same contract by
   subclassing `AiSummaryPortContract`.
4. **Unavailability as data** — the #138 lesson: a provider the adapter
   cannot reach must produce `status="unavailable"` and must not raise, and
   the guard is proven by asserting the provider was never touched, not just
   that a value came back.

Member names in these tests are synthetic logins (`octo-fixture`,
`sigma-contrib`): no real person is named. Addresses are `example.com`
placeholders, which is also what the address-shaped-key guard is tested
with. The port is a pure interface — no provider exists behind it — so
unlike the extraction phases there is no real corpus to drive; the fake
adapter below is the named synthetic fixture covering every path, per the
phase rules in issue #20.
"""

from __future__ import annotations

import importlib.util
import inspect
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints

import pytest

from coops.domain import TenantId
from coops.domain.ports import (
    SUMMARY_STATUSES,
    AiSummaryPort,
    MemberAnalyses,
    MemberAnalysis,
    MemberSummary,
    validate_member,
    validate_member_summaries,
    validate_summary_status,
)

PORT_METHODS = ("summarize_members",)

#: Provider tokens that must never appear in the port module — not in code,
#: not in docstrings. "genai" is a substring of the SDK's module path, so it
#: catches the import; "model" is deliberately absent: the docstring must be
#: able to *state* the rule ("no model identifier") without tripping it.
FORBIDDEN_TOKENS = ("gemini", "genai", "generativelanguage", "api_key", "apikey")


class InMemoryAiSummary:
    """Reference `AiSummaryPort`: render deterministic analyses, per tenant.

    The per-(tenant, member) cache is what makes tenant scoping *observable*
    in the contract tests — the same role the raw layer's tenant-keyed
    documents play for `MongoRawStore`. A wrong implementation that keyed the
    cache by member alone would hand tenant A's analysis to tenant B, and
    `test_same_member_under_two_tenants_keeps_their_analyses_apart` goes red.

    `available=False` is the unavailable provider: the call must answer
    `status="unavailable"` without raising and without touching the provider.
    `provider_calls` records every render, so tests can assert the *effect*
    — a guard that failed closed must show no call was made, not merely that
    an error was raised.
    """

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.provider_calls: list[tuple[str, str]] = []
        self._cache: dict[tuple[str, str], MemberAnalysis] = {}

    def _render(self, tenant: TenantId, summary: MemberSummary) -> MemberAnalysis:
        # The one place the "provider" is touched. Deterministic text from
        # the projected data, so round-trip tests assert content, not shape.
        self.provider_calls.append((str(tenant), summary.member))
        analysis = MemberAnalysis(
            member=summary.member,
            commits_analysis=f"commits of {summary.member}: {summary.data}",
            prs_analysis=f"pull requests of {summary.member}: {summary.data}",
            issues_analysis=f"issues of {summary.member}: {summary.data}",
        )
        self._cache[(str(tenant), summary.member)] = analysis
        return analysis

    def summarize_members(
        self,
        tenant: TenantId,
        summaries: Sequence[MemberSummary],
    ) -> MemberAnalyses:
        # Input validation happens before anything else — invalid input is a
        # caller bug even when the provider is down.
        requested = validate_member_summaries(summaries)
        if not self.available:
            return MemberAnalyses(
                tenant=tenant,
                status="unavailable",
                requested=len(requested),
                analyses=(),
            )
        analyses = tuple(
            self._cache.get((str(tenant), s.member)) or self._render(tenant, s)
            for s in requested
        )
        return MemberAnalyses(
            tenant=tenant,
            status="complete",
            requested=len(requested),
            analyses=analyses,
        )


class TestPortShape:
    """The tenant guard is the signature: assert it, so it cannot be edited away."""

    @staticmethod
    def _parameters(method_name: str) -> dict[str, inspect.Parameter]:
        parameters = inspect.signature(getattr(AiSummaryPort, method_name)).parameters
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
        hints = get_type_hints(getattr(AiSummaryPort, method_name))
        assert hints["tenant"] is TenantId

    @pytest.mark.parametrize("method_name", PORT_METHODS)
    def test_no_quota_or_wiring_parameter(self, method_name):
        # Batching, throttling and wiring are adapter properties (module
        # docstring): a caller passing them is a caller that knows the
        # provider. Assert they never enter the signature.
        parameters = self._parameters(method_name)
        assert set(parameters) == {"tenant", "summaries"}

    def test_reference_fake_satisfies_the_protocol(self):
        assert isinstance(InMemoryAiSummary(), AiSummaryPort)

    def test_a_non_conforming_object_does_not_satisfy_the_protocol(self):
        # runtime_checkable is structural: it checks method presence, not
        # signatures — which is why the signature assertions above exist.
        # (On CPython 3.12+ even a non-callable attribute named
        # ``summarize_members`` satisfies isinstance, so presence is all it
        # can ever prove — the contract suite below is the real conformance
        # check.)
        class WrongName:  # close, but the method is named differently
            def summarise_members(self, tenant, summaries): ...

        class Empty:
            pass

        assert not isinstance(object(), AiSummaryPort)
        assert not isinstance(WrongName(), AiSummaryPort)
        assert not isinstance(Empty(), AiSummaryPort)


class TestNoProviderLeak:
    """The port module must not name the provider, in code or in prose."""

    @staticmethod
    def _source_text() -> str:
        from coops.domain.ports import ai_port

        return inspect.getsource(ai_port)

    def test_the_port_module_names_no_provider(self):
        source = self._source_text().lower()
        leaks = [token for token in FORBIDDEN_TOKENS if token in source]
        assert not leaks, f"provider tokens leaked into ai_port.py: {leaks}"

    def test_the_sweep_can_find_a_leak(self):
        # A sweep that finds nothing has proved nothing until it is shown to
        # find something (docs/definition-of-done.md). The incumbent module
        # is known to carry these tokens; the sweep must flag it. Located
        # with find_spec so the module is never *imported* — reading its
        # source must not drag the provider SDK into the test run.
        spec = importlib.util.find_spec("coops.ai_analysis.generate_members_ai")
        assert spec is not None and spec.origin is not None
        incumbent = Path(spec.origin).read_text(encoding="utf-8").lower()
        found = [token for token in FORBIDDEN_TOKENS if token in incumbent]
        assert found, "control failed: the sweep no longer detects leaks"


class TestValidateMember:
    def test_accepts_and_trims_member_keys(self):
        assert validate_member(" octo-fixture\t") == "octo-fixture"

    def test_member_keys_are_not_case_folded(self):
        # TenantId folds because org names are case-insensitive; member
        # identities are not. Folding here merges people (#151).
        assert validate_member("Octo-Fixture") == "Octo-Fixture"
        assert validate_member("Octo-Fixture") != validate_member("octo-fixture")

    @pytest.mark.parametrize("value", ["", "   ", "\t\n", None])
    def test_rejects_blank_keys(self, value):
        with pytest.raises(ValueError, match="non-empty"):
            validate_member(value)

    def test_rejects_an_address_shaped_key_without_echoing_it(self):
        # The key reaches a published artifact and the domain's label policy
        # blanks address-shaped names (#132) — and the error must not itself
        # republish the address.
        with pytest.raises(ValueError, match="address-shaped") as exc_info:
            validate_member("person@example.com")
        assert "person@example.com" not in str(exc_info.value)

    def test_allows_a_name_that_merely_contains_an_address(self):
        # Whole-value match only, the twin rule of
        # coops.domain.models.actor._is_address: attribution matters.
        assert validate_member("ops contact ops@example.org") == (
            "ops contact ops@example.org"
        )


class TestMemberSummary:
    def test_is_frozen(self):
        summary = MemberSummary(member="octo-fixture", data={"total_commits": 3})
        with pytest.raises(FrozenInstanceError):
            summary.__setattr__("member", "other")

    def test_normalises_the_member_key(self):
        assert MemberSummary(member=" octo-fixture ", data=[]).member == (
            "octo-fixture"
        )

    def test_rejects_a_blank_member_key(self):
        with pytest.raises(ValueError, match="non-empty"):
            MemberSummary(member="  ", data={"total_commits": 3})

    def test_rejects_an_address_shaped_member_key(self):
        with pytest.raises(ValueError, match="address-shaped"):
            MemberSummary(member="person@example.com", data={"n": 1})

    def test_rejects_no_data(self):
        # None is not a summary: a member with nothing projected should not
        # be passed, and the value must not pretend otherwise.
        with pytest.raises(ValueError, match="needs projected data"):
            MemberSummary(member="octo-fixture", data=None)


class TestMemberAnalysis:
    def _analysis(self, **overrides) -> MemberAnalysis:
        kwargs: dict = {
            "member": "octo-fixture",
            "commits_analysis": "did commits",
            "prs_analysis": "did pull requests",
            "issues_analysis": "did issues",
        }
        kwargs.update(overrides)
        return MemberAnalysis(**kwargs)

    def test_is_frozen(self):
        with pytest.raises(FrozenInstanceError):
            self._analysis().__setattr__("commits_analysis", "rewritten")

    def test_trims_the_analysis_texts(self):
        analysis = self._analysis(commits_analysis="  did commits \n")
        assert analysis.commits_analysis == "did commits"

    @pytest.mark.parametrize(
        "field",
        ["commits_analysis", "prs_analysis", "issues_analysis"],
    )
    def test_rejects_a_blank_analysis_text(self, field):
        # Absence is an absent member, never placeholder or empty text —
        # empty text is indistinguishable from an analysis downstream.
        with pytest.raises(ValueError, match=f"empty {field}"):
            self._analysis(**{field: "   "})

    def test_rejects_a_blank_member_key(self):
        with pytest.raises(ValueError, match="non-empty"):
            self._analysis(member=" ")


class TestMemberAnalyses:
    def _analyses(self, count: int) -> tuple[MemberAnalysis, ...]:
        return tuple(
            MemberAnalysis(
                member=f"contrib-{index}",
                commits_analysis=f"c{index}",
                prs_analysis=f"p{index}",
                issues_analysis=f"i{index}",
            )
            for index in range(count)
        )

    def _result(self, status: str, requested: int, count: int) -> MemberAnalyses:
        return MemberAnalyses(
            tenant=TenantId("org-a"),
            status=status,
            requested=requested,
            analyses=self._analyses(count),
        )

    def test_is_frozen_and_normalises_analyses_to_a_tuple(self):
        result = self._result(status="complete", requested=2, count=2)
        with pytest.raises(FrozenInstanceError):
            result.__setattr__("status", "partial")
        assert isinstance(result.analyses, tuple)

    def test_carries_the_domain_tenant(self):
        result = self._result(status="complete", requested=0, count=0)
        assert isinstance(result.tenant, TenantId)
        assert result.tenant == TenantId("org-a")

    def test_accepts_the_three_valid_statuses(self):
        assert sorted(SUMMARY_STATUSES) == ["complete", "partial", "unavailable"]
        assert validate_summary_status("partial") == "partial"

    def test_rejects_an_unknown_status(self):
        with pytest.raises(ValueError, match="unknown summary status"):
            self._result(status="failed", requested=1, count=0)

    def test_rejects_a_negative_requested_count(self):
        with pytest.raises(ValueError, match=">= 0"):
            self._result(status="complete", requested=-1, count=0)

    @pytest.mark.parametrize(
        "status, requested, count",
        [
            # complete: exactly what was asked for.
            ("complete", 2, 2),
            ("complete", 0, 0),  # an empty request is vacuously complete
            # partial: some, but not all.
            ("partial", 3, 1),
            ("partial", 2, 1),
            # unavailable: none produced, and something was actually asked.
            ("unavailable", 2, 0),
        ],
    )
    def test_accepts_self_consistent_outcomes(self, status, requested, count):
        result = self._result(status=status, requested=requested, count=count)
        assert result.status == status
        assert len(result.analyses) == count

    @pytest.mark.parametrize(
        "status, requested, count",
        [
            # complete that dropped members.
            ("complete", 3, 2),
            ("complete", 1, 0),
            # partial that is really complete or really unavailable.
            ("partial", 2, 2),
            ("partial", 2, 0),
            ("partial", 1, 0),
            # unavailable that produced something, or nothing was asked.
            ("unavailable", 2, 1),
            ("unavailable", 0, 0),
        ],
    )
    def test_rejects_outcomes_that_lie_about_their_coverage(
        self, status, requested, count
    ):
        # The pipeline branches on the status to decide whether to finish
        # without summaries; an outcome that misreports its own coverage
        # turns that decision into a silent drop or a phantom outage.
        with pytest.raises(ValueError, match=status):
            self._result(status=status, requested=requested, count=count)

    def test_rejects_duplicate_members_in_the_result(self):
        first = MemberAnalysis(
            member="octo-fixture",
            commits_analysis="c",
            prs_analysis="p",
            issues_analysis="i",
        )
        with pytest.raises(ValueError, match="duplicate member"):
            MemberAnalyses(
                tenant=TenantId("org-a"),
                status="complete",
                requested=2,
                analyses=(first, first),
            )


class TestValidateMemberSummaries:
    def test_passes_the_empty_request_through(self):
        assert validate_member_summaries([]) == ()

    def test_returns_a_tuple_and_preserves_order(self):
        summaries = [
            MemberSummary(member="octo-fixture", data={"n": 1}),
            MemberSummary(member="sigma-contrib", data={"n": 2}),
        ]
        assert validate_member_summaries(summaries) == tuple(summaries)

    def test_rejects_a_duplicate_member(self):
        # Case is identity here (not folded like TenantId), so only the
        # exact same key is a duplicate.
        summaries = [
            MemberSummary(member="octo-fixture", data={"n": 1}),
            MemberSummary(member="Octo-Fixture", data={"n": 2}),
            MemberSummary(member="octo-fixture", data={"n": 3}),
        ]
        with pytest.raises(ValueError, match="duplicate member"):
            validate_member_summaries(summaries)


class AiSummaryPortContract:
    """Behaviours every `AiSummaryPort` implementation must hold.

    Subclass and implement `make_port`. The unavailability tests are the
    point of this port (#138): an adapter whose provider is unreachable must
    answer with a well-defined empty outcome — and must not touch the
    provider on the way — so the pipeline can finish without summaries.

    ``available`` and ``provider_calls`` are the observability hooks these
    tests need: an implementation under this contract exposes a way to force
    the provider down and a record of every provider interaction, so the
    fail-closed guards can be asserted as effects, not just as return values.
    """

    def make_port(self) -> AiSummaryPort:
        raise NotImplementedError

    @pytest.fixture
    def port(self) -> AiSummaryPort:
        return self.make_port()

    def test_members_in_summaries_out(self, port):
        summaries = [
            MemberSummary(member="octo-fixture", data={"total_commits": 12}),
            MemberSummary(member="sigma-contrib", data={"total_prs": 4}),
        ]
        result = port.summarize_members(TenantId("org-a"), summaries)

        assert result.status == "complete"
        assert result.requested == 2
        assert result.tenant == TenantId("org-a")
        assert [a.member for a in result.analyses] == [
            "octo-fixture",
            "sigma-contrib",
        ]
        # Content, not shape: each analysis reflects the member's own data.
        for analysis, summary in zip(result.analyses, summaries):
            assert str(summary.data) in analysis.commits_analysis
            assert str(summary.data) in analysis.prs_analysis
            assert str(summary.data) in analysis.issues_analysis

    def test_an_empty_request_is_vacuously_complete_and_touches_no_provider(self, port):
        result = port.summarize_members(TenantId("org-a"), [])
        assert result.status == "complete"
        assert result.requested == 0
        assert result.analyses == ()
        assert port.provider_calls == []

    def test_provider_unavailable_returns_data_and_does_not_raise(self, port):
        # The RawStore invariant, one layer up: a missing credential or an
        # unreachable provider must let the pipeline finish without
        # summaries. Asserted as an effect — no provider call happened —
        # not merely as a returned value.
        port.available = False
        summaries = [MemberSummary(member="octo-fixture", data={"n": 1})]

        result = port.summarize_members(TenantId("org-a"), summaries)

        assert result.status == "unavailable"
        assert result.requested == 1
        assert result.analyses == ()
        assert port.provider_calls == []

    def test_validation_precedes_the_availability_check(self, port):
        # Invalid input is a caller bug even when the provider is down; the
        # adapter must not use "unavailable" to swallow it.
        port.available = False
        summaries = [
            MemberSummary(member="octo-fixture", data={"n": 1}),
            MemberSummary(member="octo-fixture", data={"n": 2}),
        ]
        with pytest.raises(ValueError, match="duplicate member"):
            port.summarize_members(TenantId("org-a"), summaries)

    def test_duplicate_members_reach_no_provider_and_return_nothing(self, port):
        # Fail closed: not just the raise — the provider must not have been
        # called and nothing may be cached for a later call to pick up.
        summaries = [
            MemberSummary(member="octo-fixture", data={"n": 1}),
            MemberSummary(member="octo-fixture", data={"n": 2}),
        ]
        with pytest.raises(ValueError, match="duplicate member"):
            port.summarize_members(TenantId("org-a"), summaries)

        assert port.provider_calls == []
        assert port.summarize_members(TenantId("org-a"), []).analyses == ()

    def test_same_member_under_two_tenants_keeps_their_analyses_apart(self, port):
        # Tenant scoping must be observable, not incidental: the same member
        # key under two tenants is two different analyses. An adapter keying
        # its work by member alone would hand tenant A's analysis to tenant B.
        tenant_a = TenantId("org-a")
        tenant_b = TenantId("org-b")
        under_a = MemberSummary(member="octo-fixture", data={"tenant": "a"})
        under_b = MemberSummary(member="octo-fixture", data={"tenant": "b"})

        result_a = port.summarize_members(tenant_a, [under_a])
        result_b = port.summarize_members(tenant_b, [under_b])

        assert result_a.analyses[0].commits_analysis != (
            result_b.analyses[0].commits_analysis
        )
        assert result_a.tenant == tenant_a
        assert result_b.tenant == tenant_b

    def test_one_tenants_analysis_is_not_served_to_another(self, port):
        # The sharper form of the same guard: tenant B asks for nothing and
        # must get nothing, even though the adapter has seen tenant A's
        # member and a member-keyed implementation would return it.
        port.summarize_members(
            TenantId("org-a"), [MemberSummary(member="octo-fixture", data={"n": 1})]
        )
        result_b = port.summarize_members(TenantId("org-b"), [])
        assert result_b.analyses == ()
        assert result_b.tenant == TenantId("org-b")


class TestInMemoryAiSummaryPort(AiSummaryPortContract):
    def make_port(self) -> AiSummaryPort:
        return InMemoryAiSummary()
