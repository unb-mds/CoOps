"""Enumerating Bronze: one place that knows the on-disk naming scheme.

``data/bronze/`` holds per-repository files. Four aggregates that repeated
every record those files already contain were retired (#170): the writers no
longer create them and remove a leftover from an earlier run where the write
used to be — see :func:`remove_aggregate`. The naming scheme they occupied::

    commits_<repo>.json        487 files, one of which is commits_all.json
    prs_<repo>.json            428 files, one of which is prs_all.json
    issues_<repo>.json         285 files, one of which is issues_all.json
    issue_events_<repo>.json   487 files, one of which is issue_events_all.json
    structure_<repo>.json      486 files, no aggregate exists today
    repo_<repo>.json           486 files, no aggregate exists today

A glob that sweeps a family without excluding its aggregate counts every record
twice. That has produced a wrong number three times on this project, so the
exclusion belongs at enumeration time, where a caller cannot forget it, rather
than in a guard each call site writes for itself.

It was written three different ways before this module existed
(``coops/ai_analysis/generate_members_ai.py``), and a fourth call site
(``coops/silver/file_language_analysis.py``) globbed ``structure_*.json`` with no
exclusion at all — correct only for as long as nobody writes a
``structure_all.json``.

Of the three guards, measuring against ``fga-eps-mds`` said two were dead: both
``"_with_stats" in name`` and ``"issue_events" in name`` excluded zero files.
**Only the second is actually dead.** ``issues_*.json`` cannot match
``issue_events_*.json`` because the prefixes diverge at character six, so that
clause is unreachable by construction. But ``_with_stats`` excluded nothing only
because the *current* extractor stopped writing those files; an earlier version
produced them, and they are enriched copies sitting beside the originals, so
dropping the guard double-counts every repository that has one. See
``DERIVED_SUFFIXES``.

That distinction is the lesson worth keeping: "excluded zero files on the corpus
I measured" and "cannot exclude anything" look identical from the outside, and
only the second licenses removing the guard. Telling them apart needs a control —
here, asking what the code *could* be pointed at rather than only what it was
pointed at that day.

An unknown family **raises**. It must never return an empty iterator: a caller
asking for ``"commit"`` (singular, a plausible typo) would glob ``commit_*.json``,
match nothing, and report zero records — no error, no warning, a number that looks
like an answer. That silent-empty shape is the failure this module exists to
prevent, so it must not be reachable through the module itself.

**A known ambiguity, deliberately not resolved here.** A repository literally
named ``all`` would be written to ``commits_all.json`` and be indistinguishable
from the aggregate. The filesystem layout simply cannot express the difference;
no enumeration rule can recover it. ``fga-eps-mds`` has no such repository. The
fix is the move onto ``StoragePort`` (#30/#33/#34), where the aggregate is not a
document at all — see ``coops.domain.ports.storage_port``, which rejects an
``_all`` entity on both read and write.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from coops.utils.data_helpers import strip_metadata

__all__ = [
    "BRONZE_FAMILIES",
    "DERIVED_SUFFIXES",
    "DOCUMENT_FAMILIES",
    "RECORD_FAMILIES",
    "aggregate_name",
    "bronze_files",
    "bronze_records",
    "bronze_repos",
    "remove_aggregate",
    "repo_of",
]

#: Every per-repository family written into ``data/bronze/``.
BRONZE_FAMILIES = frozenset(
    {"commits", "prs", "issues", "issue_events", "structure", "repo"}
)

#: Suffixes marking a file *derived* from a per-repository file rather than
#: being one. ``commits_<repo>_with_stats.json`` is an enriched copy of
#: ``commits_<repo>.json`` and sits beside it, so a family glob matches both and
#: counts that repository twice.
#:
#: The current extractor does not write these, and ``fga-eps-mds`` has none —
#: which is exactly why this nearly went missing. A repository analysed by an
#: earlier version of this pipeline still carries 142 of them in its own
#: committed Bronze (see ``data/bronze/structure_2025-2-Squad-01.json`` at
#: 4507f9a, where ``commits_2023.2_Gotinha.json`` and
#: ``commits_2023.2_Gotinha_with_stats.json`` appear side by side). "Absent from
#: the corpus I measured" is not "cannot occur".
DERIVED_SUFFIXES = ("_with_stats",)

#: Families written as a JSON *list* of records, one file per repository.
RECORD_FAMILIES = frozenset({"commits", "prs", "issues", "issue_events"})

#: Families written as a single JSON *object* — one document describing the
#: repository, not a collection of records. ``structure_<repo>.json`` is a file
#: tree; ``repo_<repo>.json`` is the repository's own metadata.
#:
#: This distinction is declared rather than sniffed because getting it wrong is
#: silent. The first version of ``bronze_records`` assumed every family was a
#: list, so iterating a ``structure`` file yielded its top-level *keys*, none of
#: which are dicts, and the function returned **zero records for 486 files**
#: without raising. Measured against the real corpus it printed a clean table
#: with ``structure 0`` and ``repo 0`` in it. That is precisely the confident
#: zero this module was written to make unreachable, reproduced inside the module
#: itself — which is why the shapes are now named, and a payload that does not
#: match its family's declared shape raises.
DOCUMENT_FAMILIES = frozenset({"structure", "repo"})


def _check_family(family: str) -> None:
    """Raise unless ``family`` is one this layout actually has.

    Named families only — never a prefix the caller made up. The message lists
    the valid ones, because "unknown family 'commit'" on its own tells the reader
    that they were wrong without telling them what would have been right.
    """
    if family not in BRONZE_FAMILIES:
        raise ValueError(
            f"unknown bronze family {family!r}; "
            f"expected one of {sorted(BRONZE_FAMILIES)}"
        )


def aggregate_name(family: str) -> str:
    """The aggregate filename for ``family`` — the file that must be excluded."""
    _check_family(family)
    return f"{family}_all.json"


def remove_aggregate(bronze_dir: Path | str, family: str) -> Path | None:
    """Remove ``family``'s retired aggregate from ``bronze_dir``, if present.

    The Bronze writers call this where the aggregate used to be written
    (#170). A regeneration runs the pipeline over an *existing*
    ``data/bronze/``, so a writer that merely stopped writing would leave the
    previous run's aggregate on disk — stale, no longer matching the
    per-repository files beside it, still matched by anything that globs the
    family, and indistinguishable from a current file. Removal belongs where
    the write was, so no one has to remember a separate cleanup step.

    Returns the path removed, or ``None`` when there was nothing to remove:
    writers call this unconditionally, including on a fresh tree.

    Exactly ``<bronze_dir>/<family>_all.json`` and nothing else. Silver's
    ``language_analysis_all.json`` shares the ``_all`` suffix, is a different
    layer's artifact and is fetched by the dashboard, so the rule must never
    widen to ``*_all.json`` at large. It inherits the ``repo named all``
    ambiguity documented above until the move onto ``StoragePort``.
    """
    _check_family(family)
    aggregate = Path(bronze_dir) / aggregate_name(family)
    if aggregate.exists():
        aggregate.unlink()
        return aggregate
    return None


def bronze_files(bronze_dir: Path | str, family: str) -> list[Path]:
    """Every per-repository file of ``family``, aggregate excluded, sorted.

    Sorted so that two runs enumerate in the same order: ``Path.glob`` follows
    directory order, which is not stable across filesystems, and an unstable
    order produces artifacts that differ byte-for-byte between runs over
    identical input (#172).

    Raises ``ValueError`` on an unknown family; never returns an empty list to
    report one.
    """
    _check_family(family)
    directory = Path(bronze_dir)
    skip = aggregate_name(family)
    return sorted(
        path
        for path in directory.glob(f"{family}_*.json")
        if path.name != skip and not _is_derived(path.name)
    )


def _is_derived(name: str) -> bool:
    """True for a file derived from a per-repository file rather than being one.

    Matched as a *suffix*, not a substring. The guard this replaces asked
    ``"_with_stats" in name``, which also excluded a repository legitimately
    named ``with_stats_repo`` — a real file wrongly dropped, in a guard meant to
    drop duplicates. Both spellings behave identically on every repository name
    that exists today, so only the precise one is safe to keep.
    """
    stem = name[: -len(".json")] if name.endswith(".json") else name
    return any(stem.endswith(suffix) for suffix in DERIVED_SUFFIXES)


def repo_of(path: Path | str, family: str) -> str:
    """The repository name encoded in a Bronze filename.

    Strips the family *prefix*, not every occurrence of it. ``str.replace`` would
    mangle a repository whose own name contains the family token — no such
    repository exists in ``fga-eps-mds`` today, which is exactly why the bug
    would ship unnoticed.
    """
    _check_family(family)
    name = Path(path).name
    prefix = f"{family}_"
    if not name.startswith(prefix) or not name.endswith(".json"):
        raise ValueError(f"{name!r} is not a {family} file")
    return name[len(prefix) : -len(".json")]


def bronze_repos(bronze_dir: Path | str, family: str) -> Iterator[tuple[str, Path]]:
    """Yield ``(repo_name, path)`` for every per-repository file of ``family``."""
    for path in bronze_files(bronze_dir, family):
        yield repo_of(path, family), path


def bronze_records(
    bronze_dir: Path | str, family: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(repo_name, record)`` across ``family``, aggregates excluded.

    ``_metadata`` sidecars are stripped, so callers receive records only. An
    iterator rather than a list because ``commits`` alone is 260,349 records
    across 486 files, and no caller needs them all resident at once.

    A file that will not parse raises rather than being skipped. Skipping would
    reduce the count silently, which is the same defect as counting the aggregate
    twice, pointing the other way — and a module whose whole purpose is to keep a
    count honest must not have a path that quietly lowers one. Callers that
    genuinely want to tolerate a corrupt artifact can catch around the iteration,
    where the decision is visible.
    """
    for repo_name, path in bronze_repos(bronze_dir, family):
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)

        if family in DOCUMENT_FAMILIES:
            if not isinstance(payload, dict):
                raise ValueError(
                    f"{path.name}: {family} is a document family, expected a JSON "
                    f"object, got {type(payload).__name__}"
                )
            yield repo_name, payload
            continue

        if not isinstance(payload, list):
            raise ValueError(
                f"{path.name}: {family} is a record family, expected a JSON list, "
                f"got {type(payload).__name__}"
            )
        for record in strip_metadata(payload):
            if isinstance(record, dict):
                yield repo_name, record
