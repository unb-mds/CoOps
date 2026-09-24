"""The AI summary port: tenant-scoped narrative analysis of member activity.

This is the port behind ``data/silver/ai/members_ai.json`` — "summarise these
members' activity for this tenant" — and it follows the shape proven by
``StoragePort`` (#23) and, before it, ``RawStore`` (#113):

- **A `TenantId` is the first, required parameter of every method**, and the
  implementation scopes everything it does to it. There is no method that
  reaches members without one, so a caller cannot have another tenant's
  members analysed by omitting a filter.
- **Provider condition crosses the boundary as data, never as an exception**
  and never as placeholder text — see *Unavailability is data* below.
- **Structural** (`typing.Protocol`), not an ABC: an implementation is any
  object with these methods.

The rule that decided every signature here: **if a caller can tell which
provider sits behind the port, the abstraction has failed.** No provider name,
no model identifier, no credential, no SDK import and no provider error type
appears in this module — the docstrings below say "the provider" and nothing
more specific. ``tests/unit/test_ai_port.py`` sweeps this file for provider
tokens (with a control proving the sweep can find a leak), so the rule is a
guard, not a hope. Rewiring the incumbent analysis step
(``coops.ai_analysis.generate_members_ai``) onto this port is a separate
change, by the same discipline that split #21 (models) from #25 (the mapper).

What stays out of the port, and why
-----------------------------------

The incumbent step mixes four concerns:

1. **what to summarise** — member summaries in, analyses out;
2. **batching** — a request budget, a batch size computed from it;
3. **throttling** — a fixed sleep between batches, a retry budget, growing
   backoff;
4. **provider wiring** — configuring the SDK with a credential and a model
   identifier.

Only (1) is this port's business, so it is all the port expresses. Batching,
throttling and wiring are properties of *a particular provider's quota and
client*, not of "summarise these members": a second adapter would have
entirely different numbers, and a caller that passes a request budget is a
caller that knows which provider it is talking to — the failure this module
exists to prevent. They belong to the adapter, injected at its construction.

Two candidates were considered and rejected for the same reason, stated here
so the next reader does not re-litigate blind:

- **a timeout/deadline parameter** — provider-independent caller policy,
  unlike batching, so it *could* sit on the port without leaking anything;
  but no caller wants one yet, and a parameter nothing reads is untested by
  construction. Add it when a consumer exists.
- **echoing the input in the result** (repos, totals) — the caller already
  holds the summaries it passed; the port returns only what it produced.

Unavailability is data, not an exception
----------------------------------------

``RawStore``'s invariant is that a down MongoDB must not break extraction
(``coops.etl.bronze_extract`` keeps the API-only path when the store cannot
open). The same applies here, one layer up: a missing credential or an
unreachable provider must leave the pipeline able to finish **without
summaries**, not abort it. Issue #138 exists because exactly this class of
guard once shipped untested; here it is a named status with contract tests.

So ``summarize_members`` reserves exceptions for **invalid input** (caller
bugs) and expresses everything about the provider in its return value:

- ``status="complete"`` — every requested member was analysed;
- ``status="partial"`` — some were; the rest are simply absent from
  ``analyses``;
- ``status="unavailable"`` — none were; the pipeline proceeds without
  summaries.

A member without an analysis is **absent from ``analyses``**, never present
with placeholder text: wording like "analysis unavailable" is caller policy
(the incumbent writes such strings today), and once it crosses the port it
becomes indistinguishable from a real analysis downstream — the dashboard
renders whatever text arrives. Absence as absence keeps that decision with
the caller.

Value-type decisions the issue text leaves open:

- **Three analysis texts, not one blob.** The published shape
  (``members_ai.json``) carries one text each for commits, pull requests and
  issues, and the dashboard's AI pages consume exactly those fields. They are
  named for the domain's own entities (:mod:`coops.domain.models`), not for
  any provider's prompt format, so fixing them keeps adapters
  interchangeable: were each adapter to invent its own fields, callers would
  depend on the adapter again.
- **``MemberSummary`` is the already-projected summary**, not the raw
  per-repository activity: the projection (totals, samples) happens in the
  caller before the port, exactly as ``StoragePort.save`` receives
  already-projected data. Its ``data`` is plain JSON (``JSONValue``) so any
  adapter can serialise it into whatever input it needs, and so changing the
  projection is not a port change.
- **Member keys are trimmed but never case-folded**, unlike ``TenantId``.
  Organisation names are case-insensitive; member identities are not —
  folding "Alex" into "alex" merges two members, and merging people is this
  project's most expensive class of defect (#151: 16 name strings each
  shared by several distinct people).
- **An address-shaped member key is rejected** rather than passed through:
  the key reaches a published artifact, and the domain's label policy
  (:func:`coops.domain.models.actor.display_name_of`) already blanks
  address-shaped names (#132) — re-key the member instead of publishing the
  address. The rejected value is deliberately not echoed in the error
  message, so the guard cannot itself become the leak.
- **Duplicate members in one request are rejected**: the caller maps results
  back by member, and a duplicate makes that mapping ambiguous (two
  summaries, one winner, decided by accident).
- **An empty request is vacuously complete**: nothing was asked, nothing was
  produced, and the provider was never needed — reporting "unavailable"
  would be a claim about a provider the call never touched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, cast, runtime_checkable

from coops.domain.models.actor import display_name_of
from coops.domain.ports.storage_port import JSONValue
from coops.domain.tenancy import TenantId

#: Outcome of one summarise call, as data (see the module docstring).
SummaryStatus: TypeAlias = Literal["complete", "partial", "unavailable"]

#: Runtime form of :data:`SummaryStatus`, for validators and implementations.
SUMMARY_STATUSES: frozenset[str] = frozenset({"complete", "partial", "unavailable"})


def validate_member(value: str) -> str:
    """Return the trimmed member key, or raise ``ValueError``.

    Implementations and value types call this on every entry path, so the
    rules hold regardless of the caller:

    - non-empty after trimming (the key is what results are mapped back by;
      there is nothing else to key on);
    - not an address-shaped name: the key reaches a published artifact and
      the domain's label policy blanks such names (#132). The value is not
      echoed in the error, so the guard cannot republish the address.
    - trimmed but **not** case-folded: member identities are case-preserving
      (``TenantId`` folds; this must not — see the module docstring).
    """
    member = (value or "").strip()
    if not member:
        raise ValueError("member key must be a non-empty string")
    if display_name_of(member) is None:
        raise ValueError(
            "member key is an address-shaped name, not a member key: re-key"
            " the member per the display-name policy"
            " (coops.domain.models.actor.display_name_of) instead of"
            " publishing the address"
        )
    return member


def validate_summary_status(status: str) -> SummaryStatus:
    """Return ``status`` when it names an outcome, else raise.

    An unknown status is not a caller bug the adapter can paper over: the
    pipeline branches on it to decide whether to finish without summaries,
    so an unrecognisable value must fail loudly at the boundary.
    """
    if status not in SUMMARY_STATUSES:
        raise ValueError(
            f"unknown summary status {status!r}: expected one of"
            f" {sorted(SUMMARY_STATUSES)}"
        )
    return cast(SummaryStatus, status)


def validate_member_summaries(
    summaries: Sequence[MemberSummary],
) -> tuple[MemberSummary, ...]:
    """Return the summaries as a tuple, or raise on duplicate members.

    Implementations call this on every ``summarize_members`` so a request
    with two summaries for one member cannot reach the provider: results are
    mapped back by member, and a duplicate would make that mapping ambiguous.

    The empty sequence is valid and passes through — see the module docstring
    for why an empty request is a complete, not an unavailable, outcome.
    """
    seen: set[str] = set()
    for summary in summaries:
        if summary.member in seen:
            raise ValueError(
                f"duplicate member {summary.member!r} in one summarise"
                " request: results map back by member and a duplicate makes"
                " the mapping ambiguous"
            )
        seen.add(summary.member)
    return tuple(summaries)


@dataclass(frozen=True, slots=True)
class MemberSummary:
    """One member's already-projected activity summary, going into the port.

    ``data`` is plain JSON-shaped data — the projection (totals, samples of
    commits, pull requests, issues) happened in the caller, as with
    ``StoragePort.save``. The port does not interpret it, so changing the
    projection is not a port change, and any adapter can render it into
    whatever input it needs. ``None`` is not a summary: a member with no
    activity projected is a caller decision to omit, not a value to carry.

    Frozen is shallow, as with ``StoredDataset``: ``data`` is plain JSON data
    and may hold mutable lists inside.
    """

    member: str
    data: JSONValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "member", validate_member(self.member))
        if self.data is None:
            raise ValueError(
                f"MemberSummary for {self.member!r} needs projected data:"
                " None is not a summary — a member with nothing projected"
                " should simply not be passed"
            )


@dataclass(frozen=True, slots=True)
class MemberAnalysis:
    """One member's analyses, coming out of the port.

    Three texts, named for the domain's entities, because that is the
    published shape the dashboard consumes (see the module docstring). Each
    text must be non-empty after trimming: an analysis the provider did not
    produce is expressed by the member being **absent** from the result,
    never by empty or placeholder text, which would be indistinguishable
    from a real analysis downstream.
    """

    member: str
    commits_analysis: str
    prs_analysis: str
    issues_analysis: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "member", validate_member(self.member))
        for label, text in (
            ("commits_analysis", self.commits_analysis),
            ("prs_analysis", self.prs_analysis),
            ("issues_analysis", self.issues_analysis),
        ):
            stripped = (text or "").strip()
            if not stripped:
                raise ValueError(
                    f"MemberAnalysis for {self.member!r} has empty"
                    f" {label}: a missing analysis is an absent member, not"
                    " empty or placeholder text"
                )
            object.__setattr__(self, label, stripped)


@dataclass(frozen=True, slots=True)
class MemberAnalyses:
    """The outcome of one ``summarize_members`` call, as data.

    Carries the tenant (the ``StoredDataset`` pattern: a value the port
    returns is stamped with the scope it was produced under), the status the
    pipeline branches on, and the analyses actually produced. ``requested``
    exists so the outcome is **self-consistent by construction**: it is the
    count the caller asked for, and ``__post_init__`` checks the status
    against it, so an adapter cannot claim ``complete`` while silently
    dropping members, nor ``unavailable`` for a request it never made:

    ==================  =====================================
    status              invariant (all three must hold)
    ==================  =====================================
    ``complete``        ``len(analyses) == requested``
    ``partial``         ``0 < len(analyses) < requested``
    ``unavailable``     ``len(analyses) == 0 and requested > 0``
    ==================  =====================================

    Analyses are normalised to a tuple and must not repeat a member (the
    output side of the ambiguity :func:`validate_member_summaries` rejects
    on the input side).
    """

    tenant: TenantId
    status: SummaryStatus
    requested: int
    analyses: Sequence[MemberAnalysis]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", validate_summary_status(self.status))
        if self.requested < 0:
            raise ValueError(f"requested must be >= 0, got {self.requested}")
        analyses = tuple(self.analyses)
        seen: set[str] = set()
        for analysis in analyses:
            if analysis.member in seen:
                raise ValueError(
                    f"duplicate member {analysis.member!r} in the result:"
                    " the caller maps results back by member and a"
                    " duplicate makes the mapping ambiguous"
                )
            seen.add(analysis.member)
        produced = len(analyses)
        if self.status == "complete" and produced != self.requested:
            raise ValueError(
                "status 'complete' requires every requested member's"
                f" analysis: {produced} produced for {self.requested}"
                " requested"
            )
        if self.status == "partial" and not 0 < produced < self.requested:
            raise ValueError(
                "status 'partial' requires some but not all requested"
                f" members: {produced} produced for {self.requested}"
                " requested"
            )
        if self.status == "unavailable" and (produced or not self.requested):
            raise ValueError(
                "status 'unavailable' requires no analyses and a non-empty"
                f" request: {produced} produced for {self.requested}"
                " requested"
            )
        object.__setattr__(self, "analyses", analyses)


@runtime_checkable
class AiSummaryPort(Protocol):
    """Tenant-scoped narrative analysis of member activity.

    Every method requires a `TenantId` as its first parameter and the
    implementation scopes everything it does to it, so a caller cannot have
    another tenant's members analysed by omitting a filter — there is no
    method that reaches members without one.

    The single method is the whole port on purpose: what to summarise is the
    only concern that is not a property of some particular provider's quota
    or client (module docstring). Batching, throttling and wiring live in
    the adapter and its construction, never in a call's arguments.
    """

    def summarize_members(
        self,
        tenant: TenantId,
        summaries: Sequence[MemberSummary],
    ) -> MemberAnalyses:
        """Analyse ``summaries`` under ``tenant`` and return the outcome.

        The contract every implementation must hold:

        - **Provider condition is data, not an exception.** A missing
          credential, an unreachable provider or an exhausted budget returns
          ``status="unavailable"`` (or ``"partial"`` if some members were
          analysed) with the unanalysed members **absent** from
          ``analyses`` — it must not raise, so the pipeline can finish
          without summaries, and it must not emit placeholder text (an
          absent member is the only representation of a missing analysis).
          Exceptions are for invalid input only: an implementation raises
          ``ValueError`` for a duplicate member in ``summaries``
          (:func:`validate_member_summaries`) and for anything the value
          types' constructors reject.
        - **Only asked-for members appear.** Every analysis in the result is
          for a member in ``summaries``, under the calling tenant.
        - **The outcome is self-consistent** as checked by
          :class:`MemberAnalyses` — an adapter cannot report ``complete``
          while dropping members.
        - **An empty request is vacuously complete**: no provider was
          needed, so none was touched.
        """
        ...
