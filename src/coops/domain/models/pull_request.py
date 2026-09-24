"""A pull request: the PR half of GitHub's shared issues endpoint.

The provider marks a payload as a pull request with a ``pull_request``
object; everything else is the shared conversation field set (see
:mod:`coops.domain.models.issue`). PR-only facts carried here:
``merged_at`` (from the ``pull_request`` object) and ``draft``. As on
``Issue``, ``body`` and ``milestone`` are deliberately absent — free text
no consumer reads, and the channels that leaked addresses into published
data (#133).
"""

from __future__ import annotations

from dataclasses import dataclass

from coops.domain.models.issue import _Conversation


@dataclass(frozen=True, slots=True)
class PullRequest(_Conversation):
    """One pull request on one repository."""

    merged_at: str | None = None
    draft: bool = False
