import math
from typing import List, Optional

from coops.utils.github_api import GitHubAPIClient, OrganizationConfig, save_json_data, load_json_data
from coops.utils.data_helpers import strip_metadata
from coops.bronze.watermarks import WatermarkStore, max_iso, query_since
from coops.bronze.files import remove_aggregate

# ---------------------------------------------------------------------------
# What we keep from an issue or pull request.
#
# `data/bronze/` is a publish boundary: in fork-and-forget mode the pipeline
# commits it to a public branch. So the stored record is BUILT from named
# fields rather than copied from the provider and trimmed — a denylist only
# removes what someone has already noticed, and issue bodies are free text
# people paste addresses into.
#
# Every module that loads data/bronze/{issues,prs}_*.json, and what it reads:
#   silver/temporal_analysis       number, state, created_at, updated_at, closed_at, user
#   silver/members_statistics      state, created_at, updated_at, user
#   silver/collaboration_networks  user, assignee
#   silver/contribution_metrics    user, assignee
#   ai_analysis/generate_members_ai  title, state, created_at, user
#
# Deliberately absent: `body` and `milestone`, which no consumer reads and
# which carried every address found in the published data (see #133).
# ---------------------------------------------------------------------------
ISSUE_FIELDS = ("number", "state", "title", "created_at", "updated_at", "closed_at")

# Actor objects are trimmed too. A provider user object carries more than the
# login — `gravatar_id` is historically md5(email) — so stopping the whitelist
# at the top level would leave the same problem one level down.
ACTOR_FIELDS = ("login", "id", "name")


def _project_actor(actor):
    """Keep only the identity fields; preserve None so 'unassigned' stays distinct."""
    if not isinstance(actor, dict):
        return None
    return {k: actor[k] for k in ACTOR_FIELDS if k in actor}


def _project_issue(issue, repo_name):
    """Build the stored record from named fields. Never spread the response."""
    record = {k: issue[k] for k in ISSUE_FIELDS if k in issue}
    record["user"] = _project_actor(issue.get("user"))
    record["assignee"] = _project_actor(issue.get("assignee"))
    record["repo_name"] = repo_name
    return record


def _project_event(event, repo_name):
    """Keep only the event fields Silver reads; drop everything else.

    The full event payload is large and carries fields no consumer reads, so
    this projection is what keeps ``issue_events_*.json`` from growing without
    bound.
    """
    actor = event.get("actor")
    issue = event.get("issue")
    return {
        "id": event.get("id"),
        "event": event.get("event"),
        "created_at": event.get("created_at"),
        "repo_name": repo_name,
        "actor": {"login": actor.get("login")} if actor else None,
        "issue": {"number": issue.get("number")} if issue else None,
    }


def _load_prior_records(path: str, project=None) -> List[dict]:
    """Load a previously written bronze list, dropping the leading ``_metadata``.

    Returns ``[]`` when the file is missing or empty, so an incremental run over
    a repository whose previous run produced nothing still yields a fresh full
    extraction for that repository.

    ``project`` re-applies the field whitelist to every record read back. Without
    it the whitelist is only a guarantee about *writes*: an incremental run keeps
    prior records verbatim, so a record the provider never updates again would
    carry its original shape forever, and narrowing ``ISSUE_FIELDS`` would only
    take effect for records that happen to change upstream. Re-projecting on load
    makes the file self-healing — the whole file converges on the current
    whitelist at the next run, not just the rows that moved.

    Both projections are idempotent (verified over 4,057 issue and 4,171 event
    records), so this costs nothing on data that is already clean.
    """
    data = load_json_data(path)
    if not isinstance(data, list):
        return []
    records = strip_metadata(data)
    if project is None:
        return records
    return [
        project(record, record.get("repo_name"))
        for record in records
        if isinstance(record, dict)
    ]


def _merge_by_number(prior: List[dict], fresh: List[dict]) -> List[dict]:
    """Merge issue/PR records by ``number``; a fresh record replaces its prior twin."""
    merged = {item["number"]: item for item in prior if isinstance(item, dict) and "number" in item}
    for item in fresh:
        if isinstance(item, dict) and "number" in item:
            merged[item["number"]] = item
    return sorted(merged.values(), key=lambda item: item["number"])


def _fetch_events_after(client, full_name: str, last_event_id: int, use_cache: bool) -> List[dict]:
    """Fetch issue events with ``id`` greater than ``last_event_id``.

    The repository issue-events endpoint has no ``since`` filter (a ``since``
    query is accepted but ignored), so incrementality must come from the id:
    events are returned newest-first, so we page forward from the newest page
    until we reach an event whose id is ``<= last_event_id``, then stop. A
    repository with no new events costs exactly one page.
    """
    newer: List[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{full_name}/issues/events?per_page=100&page={page}"
        data = client.get_with_cache(url, use_cache)
        if not isinstance(data, list) or not data:
            break
        for event in data:
            eid = event.get("id")
            if eid is None or eid > last_event_id:
                newer.append(event)
            else:
                return newer  # reached the boundary; everything later is older
        if len(data) < 100:
            break
        page += 1
    return newer


def extract_issues(
    client: GitHubAPIClient,
    config: OrganizationConfig,
    use_cache: bool = True,
    max_issues: Optional[int] = None,
    max_prs: Optional[int] = None,
    watermarks: Optional[WatermarkStore] = None,
) -> List[str]:
    """
    Extract issues, pull requests, and issue events from GitHub repositories.

    OPTIMIZATION NOTE: Issue events are filtered to include only essential fields
    (id, event, created_at, repo_name, actor.login, issue.number) to significantly
    reduce file size. This is important for organizations with many issues/events,
    where the full event data can exceed hundreds of MB.

    The Silver layer only uses these specific fields, so filtering at Bronze layer
    prevents unnecessary data storage and processing overhead.

    max_issues/max_prs cap the number of issues/PRs kept per repo; None means
    no cap. GitHub's issues API returns issues and PRs interleaved on the same
    paginated endpoint, so the number of pages fetched is bounded only when
    both caps are set (by the larger one): capping just one of them must not
    truncate the other.

    When ``watermarks`` is given and a repository has a ``last_updated_at`` /
    ``last_event_id``, extraction is incremental for that repository: the REST
    ``since`` filter returns only items updated after the last run, which are
    then merged by number (issues/PRs) or appended by id (events). Records are
    stored sorted by number/id so a full extraction and an incremental one
    produce identical ``data/``.
    """
    # Load filtered repositories
    filtered_repos = load_json_data("data/bronze/repositories_filtered.json")
    if not filtered_repos:
        print("No repositories found. Run repository extraction first.")
        return []

    generated_files = []
    all_issues = []
    all_prs = []
    all_issue_events = []

    # Skip metadata if present
    if isinstance(filtered_repos, list) and len(filtered_repos) > 0 and isinstance(filtered_repos[0], dict) and '_metadata' in filtered_repos[0]:
        filtered_repos = filtered_repos[1:]

    # Extract issues from each repository
    for repo in filtered_repos:
        if not repo or not isinstance(repo, dict):
            print(f"Skipping invalid repo entry: {repo}")
            continue

        repo_name = repo.get('name', 'unknown')
        full_name = repo.get('full_name', repo_name)
        wm = watermarks.get(full_name) if watermarks is not None else None
        # Issues/PRs and events advance on different watermarks: an issue is
        # incremental once we have seen its `updated_at`, an event once we have
        # seen its id.
        issues_incremental = bool(wm and wm.last_updated_at)
        events_incremental = bool(wm and wm.last_event_id is not None)

        print(f"Processing issues for: {repo_name}")

        # Get issues (includes PRs)
        issues_base = f"https://api.github.com/repos/{full_name}/issues?state=all"
        if issues_incremental:
            # Over-fetch the boundary second so an item updated exactly at
            # `last_updated_at` is not lost to GitHub's exclusive `since`; the
            # merge-by-number below de-duplicates it.
            issues_base += f"&since={query_since(wm.last_updated_at)}"
        max_pages = None
        if max_issues is not None and max_prs is not None:
            max_pages = max(1, math.ceil(max(max_issues, max_prs) / 100))
        issues = client.get_paginated(issues_base, use_cache=use_cache, per_page=100, max_pages=max_pages)

        # Separate issues from PRs and project them to the stored record shape.
        repo_issues = []
        repo_prs = []
        for issue in issues or []:
            if issue.get('pull_request'):
                repo_prs.append(_project_issue(issue, repo_name))
            else:
                repo_issues.append(_project_issue(issue, repo_name))

        # On an incremental run, merge the changed records over the ones already
        # stored, keyed by number (issues and PRs DO change, so replacing
        # matters). On a full run this is a no-op.
        if issues_incremental:
            repo_issues = _merge_by_number(
                _load_prior_records(f"data/bronze/issues_{repo_name}.json", _project_issue),
                repo_issues,
            )
            repo_prs = _merge_by_number(
                _load_prior_records(f"data/bronze/prs_{repo_name}.json", _project_issue),
                repo_prs,
            )
        else:
            repo_issues = sorted(repo_issues, key=lambda item: item["number"])
            repo_prs = sorted(repo_prs, key=lambda item: item["number"])

        if max_issues is not None:
            repo_issues = repo_issues[:max_issues]
        if max_prs is not None:
            repo_prs = repo_prs[:max_prs]

        all_issues.extend(repo_issues)
        all_prs.extend(repo_prs)

        # Save per-repo files
        if repo_issues:
            repo_issues_file = save_json_data(
                repo_issues,
                f"data/bronze/issues_{repo_name}.json"
            )
            generated_files.append(repo_issues_file)

        if repo_prs:
            repo_prs_file = save_json_data(
                repo_prs,
                f"data/bronze/prs_{repo_name}.json"
            )
            generated_files.append(repo_prs_file)

        # Get issue events (filter to keep only essential fields to reduce file size)
        if events_incremental:
            fetched_events = _fetch_events_after(client, full_name, wm.last_event_id, use_cache)
        else:
            fetched_events = client.get_paginated(
                f"https://api.github.com/repos/{full_name}/issues/events",
                use_cache=use_cache, per_page=100,
            )

        repo_events = [_project_event(e, repo_name) for e in fetched_events or []]
        if events_incremental:
            # Only events newer than the last one seen are appended.
            repo_events = _load_prior_records(
                f"data/bronze/issue_events_{repo_name}.json", _project_event
            ) + repo_events
        repo_events = sorted(repo_events, key=lambda item: item.get("id") or 0)

        all_issue_events.extend(repo_events)

        # Save per-repo events
        events_file = save_json_data(
            repo_events,
            f"data/bronze/issue_events_{repo_name}.json"
        )
        generated_files.append(events_file)

        # Advance the watermark for this repository.
        if watermarks is not None:
            newest_updated = None
            for item in repo_issues + repo_prs:
                newest_updated = max_iso(newest_updated, item.get("updated_at"))
            newest_event_id = max((e.get("id") or 0 for e in repo_events), default=None)
            prior_event_id = wm.last_event_id if wm else None
            new_event_id = newest_event_id if newest_event_id is not None else prior_event_id
            watermarks.update(
                full_name,
                last_updated_at=max_iso(
                    wm.last_updated_at if wm else None, newest_updated
                ),
                last_event_id=new_event_id,
            )

    # The per-repository files above are the layer's record of truth; the
    # ``_all`` aggregates written here only repeated them, and commits_all.json
    # alone was 80.6 MiB against GitHub's 100 MB push limit (#170). Remove any
    # left by an earlier run instead of writing them: a stale aggregate beside
    # current per-repository files looks current and is still counted by
    # anything that globs the family.
    for family in ("issues", "prs", "issue_events"):
        remove_aggregate("data/bronze", family)

    print(f"Extracted {len(all_issues)} issues, {len(all_prs)} PRs, {len(all_issue_events)} events")

    return generated_files
