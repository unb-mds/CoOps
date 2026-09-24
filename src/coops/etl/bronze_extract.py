#!/usr/bin/env python3
"""
Main orchestrator for Bronze layer data extraction.
Extracts raw data from GitHub API and saves to bronze layer.
"""

import argparse
import os
import sys
from datetime import datetime
from coops.infrastructure import get_settings, resolve_tenant_from_settings
from coops.utils.github_api import GitHubAPIClient, OrganizationConfig, update_data_registry
from coops.bronze.watermarks import WatermarkStore


def _write_run_summary(client: GitHubAPIClient) -> None:
    """Report cache hits/misses and the remaining REST rate limit.

    Written to stdout always, and appended to ``GITHUB_STEP_SUMMARY`` when the
    workflow provides one, so the run summary in Actions shows how the cache
    performed.
    """
    rl = client.last_rate_limit
    remaining = f"{rl['remaining']}/{rl['limit']}" if rl else "n/a"
    reset = ""
    if rl and rl.get("reset"):
        try:
            reset = datetime.fromtimestamp(int(rl["reset"])).isoformat()
        except (TypeError, ValueError):
            reset = ""

    rows = [
        "| Metric | Value |",
        "|---|---|",
        f"| Cache hits | {client.cache_hits} |",
        f"| Cache misses | {client.cache_misses} |",
        f"| REST rate limit remaining | {remaining} |",
    ]
    if reset:
        rows.append(f"| REST rate limit resets | {reset} |")

    summary = "\n".join(["## Extraction cache & rate limit", ""] + rows) + "\n"
    print("\n" + summary)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(summary)


def positive_int(value: str) -> int:
    """argparse type for caps: a cap of 0 or less would fetch nothing."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return number


def main():
    parser = argparse.ArgumentParser(description='Extract GitHub organization data to Bronze layer')
    parser.add_argument('--cache', action='store_true', help='Use cached data when available')
    parser.add_argument('--offline', action='store_true', help='Serve every response from the cache and never touch the network. A cache miss aborts the run (offline replay), so the run sees exactly what the cache holds. Implies --cache.')
    parser.add_argument('--cache-dir', default='cache', help='Directory for the API response cache (default: ./cache, relative to the current directory). Point it at the corpus when replaying, or a run from a scratch directory reads an empty cache while looking like it worked.')
    parser.add_argument('--repo', action='append', metavar='OWNER/NAME', help='Restrict the run to exactly these repositories (repeatable). Applied after the blacklist/fork filter, so a name that is unknown, blacklisted or a fork fails the run instead of being silently skipped.')
    parser.add_argument('--max-repos', type=positive_int, help='Optional hard cap of repositories to fetch')
    parser.add_argument('--max-issues', type=positive_int, help='Optional hard cap of issues per repo to fetch')
    parser.add_argument('--max-prs', type=positive_int, help='Optional hard cap of pull requests per repo to fetch')
    parser.add_argument('--commits-method', choices=['rest', 'graphql'], default='graphql', help='Extraction method for commits (REST v3 or GraphQL v4)')
    parser.add_argument('--since', help='ISO-8601 timestamp (e.g., 2024-01-01T00:00:00Z) to limit commit extraction start')
    parser.add_argument('--until', help='ISO-8601 timestamp (e.g., 2024-12-31T23:59:59Z) to limit commit extraction end')
    parser.add_argument('--max-commits-per-repo', type=positive_int, help='Optional hard cap of commits per repo to fetch (GraphQL only)')
    parser.add_argument('--commits-page-size', type=int, default=50, help='Commits page size for pagination (REST & GraphQL). Default: 50')
    parser.add_argument('--include-active-branches', action='store_true', help='Include commits from recently active branches not merged to main (GraphQL only)')
    parser.add_argument('--active-days', type=int, default=30, help='Consider branches active if updated in last N days (default: 30)')
    parser.add_argument('--time-chunks', type=int, default=3, help='Split large extractions into N time periods to avoid API overload (default: 3)')
    parser.add_argument('--skip-structure', action='store_true', help='Skip repository structure extraction')
    parser.add_argument('--capture-dir', help='Capture every raw API response (REST and GraphQL) into this directory, tenant-scoped (corpus-raw, PRIVATE)')

    args = parser.parse_args()

    cfg = get_settings()
    if not cfg.github_token or not cfg.github_org:
        print(
            "ERROR: GITHUB_TOKEN and GITHUB_ORG must be set "
            "(environment, .env or .secrets). See RUNNING_LOCALLY.md.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Starting Bronze layer extraction for organization: {cfg.github_org}")
    print(f"Started at: {datetime.now().isoformat()}")
    if args.offline:
        print("Offline mode: every response is served from the cache; a cache miss aborts the run")

    # Initialize API client
    # One tenant per run, resolved from the deployment settings (#92): the
    # capture tree is keyed by tenant (`<root>/<tenant_id>/`), not by provider
    # account, so the writer gets the tenant's slug. The infrastructure
    # resolver translates the domain's errors into the configuration
    # vocabulary ("check TENANT_MODE"); the domain itself stays free of it.
    tenant = resolve_tenant_from_settings(cfg)
    client = GitHubAPIClient(
        cfg.github_token,
        cache_dir=args.cache_dir,
        capture_dir=args.capture_dir,
        tenant_id=(str(tenant.id) if args.capture_dir else None),
        offline=args.offline,
    )
    config = OrganizationConfig(cfg.github_org)

    # Offline implies cache use: the replay must read the cache even when
    # --cache was not spelled out (the client enforces this too; keeping the
    # flag in step makes the extractors' own cache reads explicit).
    use_cache = args.cache or args.offline

    # Per-repository extraction watermarks (issue #110): loaded from disk at the
    # start of the run and written back at the end, so the next run fetches only
    # what changed. A missing/corrupt file simply yields a full extraction.
    watermark_store = WatermarkStore()

    # Raw layer (MongoDB): when MONGO_URI is set, capture responses and read
    # them back instead of the API when they are fresh enough. Best-effort — a
    # missing/down MongoDB must not stop the extraction, so a failure to open
    # the store simply leaves the client on the API-only path.
    if cfg.mongo_uri:
        try:
            from coops.storage import MongoRawStore

            client.raw_store = MongoRawStore(cfg.mongo_uri)
            client.tenant_id = tenant.id
            client.raw_max_age_seconds = cfg.raw_max_age_seconds
        except Exception as exc:  # pragma: no cover - defensive, env-dependent
            print(f"[WARN] MongoDB raw layer unavailable ({exc}); using API only.")

    try:
        # Import and run individual extractors
        from coops.bronze.repositories import extract_repositories
        from coops.bronze.issues import extract_issues
        from coops.bronze.commits import extract_commits
        from coops.bronze.members import extract_members
        from coops.bronze.repository_structure import extract_repository_structure

        # ========================================
        # STEP 1: Extract Repositories (Required First)
        # ========================================
        print("\n" + "="*60)
        print("STEP 1: Extracting repositories")
        print("="*60)
        repo_files = extract_repositories(client, config, use_cache=use_cache, max_repos=args.max_repos, repo_filter=args.repo)
        print(f"Generated {len(repo_files)} repository files")

        # ========================================
        # STEP 2: Extract Issues and Pull Requests
        # ========================================
        print("\n" + "="*60)
        print("STEP 2: Extracting issues and pull requests")
        print("="*60)
        issue_files = extract_issues(client, config, use_cache=use_cache, max_issues=args.max_issues, max_prs=args.max_prs, watermarks=watermark_store)
        print(f"Generated {len(issue_files)} issue files")

        # ========================================
        # STEP 3: Extract Commits (GraphQL/REST Hybrid)
        # ========================================
        print("\n" + "="*60)
        print("STEP 3: Extracting commits")
        print("="*60)
        commit_files = extract_commits(
            client,
            config,
            use_cache=use_cache,
            method=args.commits_method,
            since=args.since,
            until=args.until,
            max_commits_per_repo=args.max_commits_per_repo,
            page_size=args.commits_page_size,
            include_active_branches=args.include_active_branches,
            active_days=args.active_days,
            time_chunks=args.time_chunks,
            watermarks=watermark_store,
        )
        print(f"Generated {len(commit_files)} commit files")

        # ========================================
        # STEP 4: Extract Organization Members
        # ========================================
        print("\n" + "="*60)
        print("STEP 4: Extracting organization members")
        print("="*60)
        member_files = extract_members(client, config, use_cache=use_cache)
        print(f"Generated {len(member_files)} member files")

        # ========================================
        # STEP 5: Extract Repository Structure (GraphQL)
        # ========================================
        structure_files = []
        if not args.skip_structure:
            print("\n" + "="*60)
            print("STEP 5: Extracting repository structures")
            print("="*60)
            structure_files = extract_repository_structure(client, config, use_cache=use_cache, watermarks=watermark_store)
            print(f"Generated {len(structure_files)} structure files")
        else:
            print("\nSkipping repository structure extraction (--skip-structure)")

        # ========================================
        # Persist Watermarks
        # ========================================
        watermark_store.save()

        # ========================================
        # Update Registry
        # ========================================
        all_files = repo_files + issue_files + commit_files + member_files + structure_files
        update_data_registry('bronze', 'all_extractions', all_files)

        print("\n" + "="*60)
        print(f"SUCCESS: Bronze extraction completed!")
        print("="*60)
        print(f"Total files generated: {len(all_files)}")
        print(f"   - Repositories: {len(repo_files)}")
        print(f"   - Issues/PRs: {len(issue_files)}")
        print(f"   - Commits: {len(commit_files)}")
        print(f"   - Members: {len(member_files)}")
        print(f"   - Structures: {len(structure_files)}")
        print("="*60)

        _write_run_summary(client)

    except Exception as e:
        print(f"\nERROR: Bronze extraction failed")
        print(f"   {str(e)}")
        import traceback
        traceback.print_exc()
        try:
            _write_run_summary(client)
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()
