"""Ports: the interfaces the pipeline depends on, not the drivers behind them.

A port is a structural (`typing.Protocol`) interface with the tenant scope
built into every method, so an implementation cannot be reached without it.
Implementations live elsewhere — ``coops.storage`` today, adapters per #39 —
and nothing under ``coops.domain`` imports a driver or provider library.
"""

from .ai_port import (
    SUMMARY_STATUSES,
    AiSummaryPort,
    MemberAnalyses,
    MemberAnalysis,
    MemberSummary,
    SummaryStatus,
    validate_member,
    validate_member_summaries,
    validate_summary_status,
)
from .source_port import (
    SourceAccessError,
    SourceError,
    SourceNotFoundError,
    SourcePort,
    SourceUnavailableError,
    validate_branch,
    validate_repo_name,
)
from .storage_port import (
    LAYERS,
    JSONValue,
    Layer,
    StoragePort,
    StoredDataset,
    validate_entity,
    validate_layer,
)

__all__ = [
    "LAYERS",
    "SUMMARY_STATUSES",
    "AiSummaryPort",
    "JSONValue",
    "Layer",
    "MemberAnalyses",
    "MemberAnalysis",
    "MemberSummary",
    "SourceAccessError",
    "SourceError",
    "SourceNotFoundError",
    "SourcePort",
    "SourceUnavailableError",
    "StoragePort",
    "StoredDataset",
    "SummaryStatus",
    "validate_branch",
    "validate_entity",
    "validate_layer",
    "validate_member",
    "validate_member_summaries",
    "validate_repo_name",
    "validate_summary_status",
]
