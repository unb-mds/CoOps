"""Silver's one representation of a record no identity channel attributes.

Measured on the real corpora (issues #154 and #155 — one defect, two
symptoms):

- 2,076 of 56,488 commits (3.7%) carry ``login``, ``name``, ``email`` and
  ``author_email_hash`` *all* null — a capture scrubbed before the hash
  that replaced the address existed, so those authors are unrecoverable
  at any price.
- 957 of 298,395 issue events carry ``"actor": null`` — GitHub's answer
  for an account that has since been deleted.

Silver's two consumers of those records each improvised, differently:
``members_statistics`` excluded the literal identity ``'unknown'`` (a
silent drop — 3.7% of commits contributed to no member at all), while
``temporal_analysis`` had no such exclusion (a phantom contributor
literally named ``unknown``, credited with 1,081 events and rendered by
three dashboard pages). The root cause was ``or {}`` / ``or 'unknown'``
doing double duty: *"the key is missing"* and *"the provider told us
there is nobody"* are different facts, and the second was discarded.

The answer is the one the domain layer already settled for the same
defect (``domain/models/actor.py`` and ``github/mapper.py``, #165/#151):
a person no channel identifies is **absent** — ``Commit.author is None``,
never an ``Actor`` carrying a blank field, because a shared empty
identity would merge distinct people. JSON cannot be absent, so Silver
writes absence explicitly:

- the identity is ``None`` (JSON ``null``), never a placeholder string —
  a truthy sentinel becomes a person downstream, which is how
  ``unknown`` came to hold 1,081 events;
- the record carries the boolean marker :data:`UNATTRIBUTED_FIELD` — a
  field a reader and the dashboard can test, not a magic name, because
  ``'unknown'`` collides with a login a real member may legitimately
  hold.

Both Silver modules resolve identity through this module, so they cannot
disagree again: one chain per record kind, ``None`` terminus, and the
records are carried in an explicit unattributed bucket on both sides —
dropping 3.7% of commits would leave every per-member total quietly
short with nothing on the dashboard saying so.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The boolean marker that distinguishes an unattributed record from
#: every real member. A field, not a magic name: consumers (and the
#: dashboard, when it renders the bucket) test
#: ``record.get(UNATTRIBUTED_FIELD)``, which no login can collide with.
UNATTRIBUTED_FIELD = 'unattributed'

__all__ = [
    'UNATTRIBUTED_FIELD',
    'commit_author_identity',
    'conversation_actor_identity',
    'is_unattributed',
    'mark_unattributed',
]


def is_unattributed(record: Mapping[str, Any]) -> bool:
    """True when *record* carries the unattributed marker.

    The reader-facing half of the contract: everything downstream that
    needs "is this a member?" asks this, so a record can never be both.
    """
    return bool(record.get(UNATTRIBUTED_FIELD))


def mark_unattributed(record: dict[str, Any]) -> dict[str, Any]:
    """Carry an event whose user is ``None`` as explicitly unattributed.

    The writer-facing half of the contract, applied at every event site
    in ``temporal_analysis``: an event nobody can be attributed to keeps
    its ``user: None`` (absence, the JSON twin of ``Commit.author is
    None``) and gains the marker, so it can never be mistaken for — or
    merged into — a person. Events with a real identity are returned
    untouched.
    """
    if record.get('user') is None:
        record[UNATTRIBUTED_FIELD] = True
    return record


def commit_author_identity(commit: Mapping[str, Any]) -> str | None:
    """Resolve a commit's author to an identity, or ``None``.

    The chain both Silver modules used to duplicate, with its terminus
    changed from the ``'unknown'`` sentinel to ``None``:
    ``commit.commit.author.login`` -> top-level ``author.login`` ->
    ``author_email_hash`` -> ``author.commit.author.name``. Every channel
    null means no channel identifies the author — an unattributed record
    (#154), never a member named ``unknown`` and never a silent drop.
    """
    author_obj = (commit.get('commit') or {}).get('author') or {}
    if author_obj.get('login'):
        return author_obj['login']
    top_author = commit.get('author') or {}
    if top_author.get('login'):
        return top_author['login']
    if author_obj.get('author_email_hash'):
        return author_obj['author_email_hash']
    if author_obj.get('name'):
        return author_obj['name']
    return None


def conversation_actor_identity(user_obj: Any) -> str | None:
    """Resolve an issue/PR ``user`` or event ``actor`` object, or ``None``.

    ``login -> name``, and ``None`` when the object is JSON ``null``
    (GitHub's answer for a deleted account, #155), absent, or carries
    neither channel. This is where ``event.get('actor') or {}`` used to
    conflate *"there is no actor object"* with *"the provider said there
    is nobody"*: both resolve to ``None`` here, and the caller carries
    the record in the unattributed bucket instead of inventing a person
    for it to belong to.
    """
    if not isinstance(user_obj, Mapping):
        return None
    return user_obj.get('login') or user_obj.get('name') or None
