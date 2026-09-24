"""Silver's Bronze input: the per-repository files, never the ``_all`` aggregates.

Four Silver processors (``members_statistics``, ``contribution_metrics``,
``collaboration_networks``, ``temporal_analysis``) each used to open the four
``data/bronze/<family>_all.json`` aggregates directly — the same four
``load_json_data`` lines copied four times over. Issue #170 retires the
aggregates: ``commits_all.json`` alone is 80.6 MiB against GitHub's 100 MB
push limit, and every record in it already exists in the per-repository file
it was concatenated from. This module is the one reader that keeps those
processors working while the aggregates go away.

The substitution is exact, not approximate. The Bronze writers build each
aggregate with ``all_<family>.extend(per_repo_records)`` over the very list
they then write to ``<family>_<repo>.json`` (``bronze/issues.py``,
``bronze/commits.py``): no field is added at concatenation time, and
``repo_name`` is stamped into each record at projection time, so it is in
the per-repository files too. What the aggregate had and this read does not
preserve is its record *order* — extractor iteration order instead of
filename-sorted enumeration — and no Silver consumer depends on input order:
every artifact is keyed, counted, min/max-reduced or re-sorted on its own
key.

Reading through :func:`coops.bronze.files.bronze_records` rather than a
local glob means the exclusion of ``<family>_all.json`` and of the derived
``_with_stats`` copies happens at enumeration time (#156), where a caller
cannot forget it — forgetting is what would silently double-count every
repository.
"""

from __future__ import annotations

from typing import Any

from coops.bronze.files import RECORD_FAMILIES, bronze_records

__all__ = ["BRONZE_DIR", "load_family"]

#: The ETL reads and writes ``./data`` relative to the working directory.
BRONZE_DIR = "data/bronze"


def load_family(family: str) -> list[dict[str, Any]]:
    """Every Bronze record of ``family``, read from the per-repository files.

    A list rather than the iterator ``bronze_records`` yields because the
    Silver processors walk their inputs more than once
    (``contribution_metrics`` counts users first and repositories second,
    over the same records) — and a partially consumed iterator would make
    the second pass silently empty.

    Accepts only the record families (lists of records), not the document
    families: iterating a ``structure_<repo>.json`` document yields its
    keys, which is the confident zero this layer must not produce.
    """
    if family not in RECORD_FAMILIES:
        raise ValueError(
            f"unknown bronze record family {family!r}; "
            f"expected one of {sorted(RECORD_FAMILIES)}"
        )
    return [
        record for _repo_name, record in bronze_records(BRONZE_DIR, family)
    ]
