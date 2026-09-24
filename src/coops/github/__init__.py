"""The GitHub provider package: everything that knows GitHub's shapes.

Only the mapper lives here today; the raw capture
(:mod:`coops.raw_capture`) and the Bronze extractors
(:mod:`coops.bronze`) predate it and are untouched.
"""

from coops.github.mapper import (
    map_activity_event,
    map_commit_graphql,
    map_commit_rest,
    map_file_tree_graphql,
    map_file_tree_rest,
    map_issue,
    map_member,
    map_pull_request,
    map_repository,
)

__all__ = [
    "map_activity_event",
    "map_commit_graphql",
    "map_commit_rest",
    "map_file_tree_graphql",
    "map_file_tree_rest",
    "map_issue",
    "map_member",
    "map_pull_request",
    "map_repository",
]
