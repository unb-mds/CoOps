#!/usr/bin/env python3
"""
Shared utilities for GitHub API interactions and data processing.

Includes REST and GraphQL support, caching, parallel commit fetching,
repository tree extraction, and organization configuration.
"""

import os
import json
import time
import hashlib
import requests
import threading
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urlsplit, parse_qsl

from coops.storage.raw import PROVIDER_GITHUB, is_fresh

class OfflineCacheMiss(RuntimeError):
    """Offline mode was asked for a URL that has no cached body.

    Offline mode exists to replay a run against a fixed cache (#199): every
    response must come from that cache, or the run must stop. A miss must not
    return ``None`` (the network-failure path) or fall through to an empty
    result — either would silently drop part of the corpus and report a
    plausible-looking partial answer. The message names the URL so the missing
    cache entry can be identified and produced.
    """

    def __init__(self, url: str):
        super().__init__(f"offline mode: no cached response for {url}")
        self.url = url

class GitHubAPIClient:
    def __init__(
        self,
        token: str,
        cache_dir: str = "cache",
        capture_dir: Optional[str] = None,
        tenant_id: Optional[Any] = None,
        provider: str = "github",
        raw_store: Optional[Any] = None,
        raw_max_age_seconds: Optional[float] = None,
        offline: bool = False,
    ):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }
        self.cache_dir = cache_dir
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        # GraphQL endpoint
        self.graphql_url = "https://api.github.com/graphql"
        # Managed raw-corpus capture (issue #109). Off unless capture_dir is
        # given; when it is, tenant_id is required because the capture shape
        # is tenant-scoped. See coops.raw_capture.capture.
        self.capture_dir = capture_dir
        self.tenant_id = tenant_id
        self.provider = provider
        self._capture = None
        if capture_dir is not None:
            if not tenant_id:
                raise ValueError("capture_dir requires a tenant_id")
            from coops.raw_capture.capture import RawCaptureWriter
            self._capture = RawCaptureWriter(capture_dir, tenant_id, provider)
        # Raw layer (MongoDB): a read-through/write-through cache in front of
        # the API. When configured, a fresh raw document short-circuits the
        # network so re-processing is free, and a successful fetch is captured
        # back into the raw layer. Both are best-effort: the raw layer is an
        # optimisation, so a down MongoDB must not break the extraction.
        self.raw_store = raw_store
        self.raw_max_age_seconds = raw_max_age_seconds
        # Offline mode (the #199 replay): when set, the cache is the only
        # source of responses. A cached body is served without revalidation
        # regardless of its ETag (a blocked network must not turn a warm,
        # revalidatable entry into `None`), and a cache miss raises
        # OfflineCacheMiss instead of falling through to a request that
        # cannot happen. Nothing is written: see get_with_cache.
        self.offline = offline
        # Run-summary accounting: a "hit" is a request served from cache (a 304
        # or a short-circuited body) without consuming a rate-limit slot; a
        # "miss" is a billed network fetch (a 200) that populates the cache.
        self.cache_hits = 0
        self.cache_misses = 0
        # Most recent REST rate-limit window (remaining/limit/reset), if any.
        self.last_rate_limit: Optional[Dict[str, Any]] = None

    # -- Raw layer (MongoDB) ---------------------------------------------
    #
    # The raw layer is a read-through/write-through cache keyed by
    # (tenant, provider, endpoint, params_hash). Reads only short-circuit the
    # network when a document is fresh enough; the tenant scope is enforced by
    # the store, so the client just forwards the tenant it was given.

    @staticmethod
    def _split_url(url: str) -> Tuple[str, Dict[str, str]]:
        """Split a URL into (endpoint without query, query params as a dict)."""
        from urllib.parse import parse_qsl, urlsplit, urlunsplit

        parts = urlsplit(url)
        endpoint = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        return endpoint, params

    def _raw_read(self, provider: str, endpoint: str, params: Dict[str, Any]) -> Optional[Any]:
        """Return a fresh raw payload for the key, or None to fall through."""
        if self.raw_store is None or self.tenant_id is None:
            return None
        try:
            document = self.raw_store.get(self.tenant_id, provider, endpoint, params)
        except Exception:
            # The raw layer is best-effort; a failure here must not stop the run.
            return None
        if document is None:
            return None
        if not is_fresh(document.fetched_at, self.raw_max_age_seconds):
            return None
        return document.payload

    def _raw_write(
        self,
        provider: str,
        endpoint: str,
        params: Dict[str, Any],
        etag: Optional[str],
        payload: Any,
    ) -> None:
        """Capture a fetched payload into the raw layer (best-effort)."""
        if self.raw_store is None or self.tenant_id is None:
            return
        try:
            self.raw_store.save(self.tenant_id, provider, endpoint, params, etag, payload)
        except Exception:
            pass

    def _get_cache_key(self, key: str) -> str:
        """Create a stable cache key from an arbitrary string."""
        return hashlib.md5(key.encode()).hexdigest() + ".json"

    def _cache_get(self, cache_key: str) -> Optional[Any]:
        """Get response from cache if exists"""
        cache_file = os.path.join(self.cache_dir, self._get_cache_key(cache_key))
        if os.path.exists(cache_file):
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def _cache_set(self, cache_key: str, data: Any) -> None:
        """Save response to cache"""
        cache_file = os.path.join(self.cache_dir, self._get_cache_key(cache_key))
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # -- ETag sidecar ------------------------------------------------------
    #
    # The response body keeps its original format in ``<md5(url)>.json``; the
    # ``ETag`` header is stored verbatim in a sibling ``<md5(url)>.etag`` file.
    # A sidecar (rather than an envelope around the body) keeps the on-disk body
    # format untouched, so entries written before ETag support — and any other
    # reader of the JSON — keep working. A body with no sidecar simply has no
    # ETag, so the client falls back to serving it directly (the pre-ETag
    # behaviour) instead of sending a conditional request. GraphQL responses
    # have no ETag and never write a sidecar.

    def _get_etag_path(self, cache_key: str) -> str:
        """Path of the ETag sidecar file for a cache key."""
        return os.path.join(
            self.cache_dir,
            hashlib.md5(cache_key.encode()).hexdigest() + ".etag",
        )

    def _etag_get(self, cache_key: str) -> Optional[str]:
        """Read the stored ETag for a cache key, or None if there is none."""
        etag_file = self._get_etag_path(cache_key)
        if not os.path.exists(etag_file):
            return None
        with open(etag_file, 'r', encoding='utf-8') as f:
            value = f.read().strip()
        return value or None

    def _etag_set(self, cache_key: str, etag: str) -> None:
        """Store the ETag for a cache key."""
        with open(self._get_etag_path(cache_key), 'w', encoding='utf-8') as f:
            f.write(etag)

    def _etag_delete(self, cache_key: str) -> None:
        """Remove the ETag sidecar for a cache key (best effort)."""
        etag_file = self._get_etag_path(cache_key)
        if os.path.exists(etag_file):
            os.remove(etag_file)

    # -- Run-summary accounting -------------------------------------------

    def _record_cache_hit(self) -> None:
        self.cache_hits += 1

    def _record_cache_miss(self) -> None:
        self.cache_misses += 1

    def _record_rate_limit(self, response: "requests.Response") -> None:
        """Remember the most recent REST rate-limit window for the run summary."""
        remaining = response.headers.get('X-RateLimit-Remaining')
        if remaining is None:
            return
        try:
            self.last_rate_limit = {
                'remaining': int(remaining),
                'limit': int(response.headers.get('X-RateLimit-Limit', '0') or 0),
                'reset': response.headers.get('X-RateLimit-Reset'),
            }
        except (TypeError, ValueError):
            pass

    # -- Raw-corpus capture (issue #109) -----------------------------------
    #
    # When capture is enabled (capture_dir + tenant_id), every REST and
    # GraphQL 200 is written as a {tenant_id, provider, endpoint, params,
    # etag, fetched_at, payload} record. The payload is unmodified and
    # contains personal data, so the writer keeps it mode 700/600 and the
    # result must never be published. Sanitization into the shareable
    # corpus-fixtures is done separately (coops.raw_capture.sanitize).

    @staticmethod
    def _split_url_path(url: str) -> Tuple[str, Dict[str, str]]:
        """Split a REST URL into its path (endpoint) and query params.

        Distinct from :meth:`_split_url`, which returns the full URL minus its
        query. The raw-corpus capture (#109) indexes on the path so that the
        same endpoint requested from different hosts collapses to one key,
        while the Mongo raw layer (#113) keys on the full URL.
        """
        parts = urlsplit(url)
        params = dict(parse_qsl(parts.query, keep_blank_values=True))
        return parts.path, params

    def _capture_rest(self, url: str, data: Any, etag: Optional[str]) -> None:
        if self._capture is None:
            return
        endpoint, params = self._split_url_path(url)
        self._capture.write(endpoint, params, etag, data)

    def _capture_graphql(self, query: str, variables: Optional[Dict[str, Any]], data: Any) -> None:
        if self._capture is None:
            return
        # GraphQL has no ETag; the query text and variables travel in params.
        self._capture.write(
            "graphql", {"query": query, "variables": variables or {}}, None, data
        )

    def get_with_cache(self, url: str, use_cache: bool = True, retries: int = 3, backoff_base: float = 1.0, return_headers: bool = False, silent: bool = False, log_prefix: str = "REST") -> Any:
        """Get data from GitHub API with caching, ETag revalidation, retries, and backoff.

        When a cached body has a stored ETag, the request is sent with
        ``If-None-Match``. A 304 then serves the cached body and does not
        consume a rate-limit slot; a 200 replaces the cached body (and its
        ETag). A cached body with no ETag (an entry written before ETag
        support, or a response that carried no ``ETag`` header) is served
        directly, without a conditional request.

        In offline mode (``offline=True``) the cache is the only source: a
        cached body is served directly — even one with an ETag, which online
        would revalidate — and a miss raises OfflineCacheMiss. ``use_cache``
        is deliberately not honoured here: offline is the strongest form of
        "use the cache", and the refresh ``use_cache=False`` asks for is
        impossible without a network. Nothing is read from or written to the
        network, so the cache-writing code below (reachable only after a
        successful response) is unreachable.
        """
        cached = None
        etag = None

        # Raw layer first: a fresh document short-circuits the API entirely, so
        # re-processing is free. The payload returned here is the *unmodified*
        # API body (it may carry personal data); the Bronze scrub runs later,
        # on the projection that is written to the public branch.
        if use_cache and self.raw_store is not None and self.tenant_id is not None:
            endpoint, params = self._split_url(url)
            raw_payload = self._raw_read(PROVIDER_GITHUB, endpoint, params)
            if raw_payload is not None:
                if not silent:
                    print(f"Using raw layer for: {url}")
                self._record_cache_hit()
                return raw_payload if not return_headers else (raw_payload, None)

        # Offline mode: the cache is the only source (the raw layer above, or
        # the file cache here). An ETag'd entry must NOT go to revalidation —
        # with the network blocked, the RequestException path below returns
        # None and the entry silently disappears from the replay (#199). A
        # miss raises so the run stops rather than producing a partial answer.
        if self.offline:
            cached = self._cache_get(url)
            if cached is None:
                raise OfflineCacheMiss(url)
            if not silent:
                print(f"Using cached data (offline) for: {url}")
            self._record_cache_hit()
            return cached if not return_headers else (cached, None)

        if use_cache:
            cached = self._cache_get(url)
            if cached is not None:
                etag = self._etag_get(url)

        # A warm body with no ETag cannot be revalidated: keep the pre-ETag
        # behaviour of serving it directly, with no network round-trip.
        if use_cache and cached is not None and etag is None:
            if not silent:
                print(f"Using cached data for: {url}")
            self._record_cache_hit()
            return cached if not return_headers else (cached, None)

        headers = dict(self.headers)
        if etag is not None:
            headers["If-None-Match"] = etag

        if not silent:
            if etag is not None:
                print(f"Revalidating with ETag for: {url}")
            else:
                print(f"Fetching from API: {url}")
        attempt = 0
        while attempt < retries:
            try:
                response = requests.get(url, headers=headers, timeout=35)

                if response.status_code == 200:
                    data = response.json()
                    self._capture_rest(url, data, response.headers.get("ETag"))
                    new_etag = response.headers.get("ETag")
                    if use_cache:
                        self._cache_set(url, data)
                        if new_etag:
                            self._etag_set(url, new_etag)
                        else:
                            self._etag_delete(url)
                    self._record_cache_miss()
                    self._record_rate_limit(response)
                    # Capture the unmodified body into the raw layer so the next
                    # run can read it instead of fetching again.
                    if use_cache and self.raw_store is not None and self.tenant_id is not None:
                        endpoint, params = self._split_url(url)
                        self._raw_write(PROVIDER_GITHUB, endpoint, params, new_etag, data)
                    if not return_headers and not silent:
                        self._log_rate_limit(response, prefix=log_prefix)
                    return data if not return_headers else (data, response.headers)
                elif response.status_code == 304:
                    # Not Modified: the cached body is still current, and a 304
                    # does not count against the rate limit.
                    if not silent:
                        print(f"304 Not Modified - serving cached data for: {url}")
                    self._record_cache_hit()
                    self._record_rate_limit(response)
                    if not return_headers and not silent:
                        self._log_rate_limit(response, prefix=log_prefix)
                    return cached if not return_headers else (cached, response.headers)
                elif response.status_code == 403:
                    print(f"[ERROR] API request forbidden (403) - might be private or rate limited: {response.text}")
                    if "rate limit" in response.text.lower():
                        print("Rate limit exceeded. Waiting 60 seconds...")
                        time.sleep(60)
                        # After sleep, continue loop to retry
                    else:
                        print("Access forbidden - resource might be private or require different permissions")
                        return None
                elif response.status_code == 404:
                    print(f"[ERROR] Resource not found (404): {url}")
                    return None
                elif 500 <= response.status_code < 600:
                    attempt += 1
                    wait = backoff_base * (2 ** (attempt - 1))
                    print(f"[WARN] API {response.status_code} - retrying in {wait:.1f}s (attempt {attempt}/{retries})")
                    time.sleep(wait)
                    continue
                else:
                    print(f"[ERROR] API request failed: {response.status_code} - {response.text}")
                    return None
            except requests.exceptions.Timeout:
                attempt += 1
                wait = backoff_base * (2 ** (attempt - 1))
                print(f"[ERROR] Request timeout for: {url} - retrying in {wait:.1f}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue
            except requests.exceptions.RequestException as e:
                print(f"[ERROR] Request error for {url}: {str(e)}")
                return None
        print(f"[ERROR] Exhausted retries for: {url}")
        return None

    # ----------------------
    # GraphQL support (API v4)
    # ----------------------
    def _graphql_cache_key(self, payload: Dict[str, Any]) -> Optional[str]:
        """Deterministic cache key for a GraphQL query + its variables."""
        try:
            return "graphql:" + hashlib.md5(
                (payload["query"] + "::" + json.dumps(payload["variables"], sort_keys=True, ensure_ascii=False)).encode("utf-8")
            ).hexdigest()
        except Exception:
            # Unserializable variables: no cache key, so no cache.
            return None

    def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None, use_cache: bool = True, timeout: int = 4) -> Any:
        """Execute a GraphQL query against GitHub's v4 API with simple timeout handling.

        In offline mode the cache is the only source, exactly as in
        get_with_cache: a cached response is served with no POST, and a miss
        raises OfflineCacheMiss naming the endpoint and the cache key. A mode
        that covered REST but not GraphQL would read as covered while
        silently dropping every commit-history page of a replay.
        """
        payload = {"query": query, "variables": variables or {}}

        # Raw layer first: a fresh document short-circuits the API.
        if use_cache and self.raw_store is not None and self.tenant_id is not None:
            raw_params = {"query": query, "variables": variables or {}}
            raw_payload = self._raw_read(PROVIDER_GITHUB, self.graphql_url, raw_params)
            if raw_payload is not None:
                print("[GRAPHQL] Using raw layer response")
                self._record_cache_hit()
                return raw_payload

        # Offline mode: same guarantee as the REST path. GraphQL bodies carry
        # no ETag, so there is no revalidation to skip — what is skipped is
        # the POST itself, and a miss raises instead of returning None.
        if self.offline:
            cache_key = self._graphql_cache_key(payload)
            cached = self._cache_get(cache_key) if cache_key else None
            if cached is None:
                raise OfflineCacheMiss(f"{self.graphql_url} (cache key {cache_key})")
            print("[GRAPHQL] Using cached response (offline)")
            self._record_cache_hit()
            return cached

        # Build a deterministic cache key based on query + variables
        cache_key = None
        if use_cache:
            try:
                cache_key = self._graphql_cache_key(payload)
                if cache_key:
                    cached = self._cache_get(cache_key)
                    if cached is not None:
                        print("[GRAPHQL] Using cached response")
                        self._record_cache_hit()
                        return cached
            except Exception:
                # Fallback to no-cache if serialization fails
                cache_key = None

        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"

        try:
            response = requests.post(self.graphql_url, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                if "errors" in data:
                    # Check if errors are SERVICE_UNAVAILABLE (commit stats unavailable)
                    errors = data.get('errors', [])
                    has_stats_unavailable = any(
                        err.get('type') == 'SERVICE_UNAVAILABLE' and
                        ('additions' in str(err.get('path', [])) or 'deletions' in str(err.get('path', [])))
                        for err in errors
                    )

                    if has_stats_unavailable:
                        # Stats unavailable - treat as failure to trigger REST fallback
                        print(f"[GRAPHQL][WARN] Commit stats unavailable (SERVICE_UNAVAILABLE)")
                        return None  # Trigger REST fallback
                    else:
                        # Other critical errors
                        print(f"[GRAPHQL][ERROR] Returned errors: {data['errors']}")
                        return None
                self._record_cache_miss()
                if use_cache and cache_key:
                    self._cache_set(cache_key, data)
                self._capture_graphql(query, variables, data)
                # Capture the unmodified body into the raw layer.
                if use_cache and self.raw_store is not None and self.tenant_id is not None:
                    self._raw_write(
                        PROVIDER_GITHUB,
                        self.graphql_url,
                        {"query": query, "variables": variables or {}},
                        None,
                        data,
                    )
                # Don't log rate limit for GraphQL - already logged after processing commits
                return data
            elif response.status_code == 403:
                if "rate limit" in response.text.lower():
                    print(f"[GRAPHQL][WARN] Rate limit exceeded")
                else:
                    print(f"[GRAPHQL][ERROR] Forbidden (403)")
                return None
            elif response.status_code == 502:
                print(f"[GRAPHQL][WARN] 502 (server overload)")
                return None
            elif response.status_code in [500, 503]:
                print(f"[GRAPHQL][WARN] {response.status_code}")
                return None
            else:
                print(f"[GRAPHQL][ERROR] Request failed: {response.status_code}")
                return None
        except requests.exceptions.Timeout:
            print(f"[GRAPHQL][WARN] Timeout ({timeout}s)")
            return None
        except requests.exceptions.RequestException as e:
            print(f"[GRAPHQL][ERROR] Request error: {str(e)}")
            return None

    def _split_time_range(
        self,
        since: Optional[str],
        until: Optional[str],
        chunks: int = 3,
    ) -> List[Tuple[Optional[str], Optional[str]]]:
        """
        Split a time range into smaller chunks for efficient extraction.

        Args:
            since: Start date (ISO format) or None
            until: End date (ISO format) or None
            chunks: Number of chunks to split into (default: 3)

        Returns:
            List of (since, until) tuples representing time ranges
        """
        from datetime import datetime, timedelta, timezone

        # If no date range specified, return single range
        if not since and not until:
            return [(None, None)]

        # Parse dates
        try:
            if since:
                start_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
            else:
                # Default to 1 year ago if not specified
                start_dt = datetime.now(timezone.utc) - timedelta(days=365)

            if until:
                end_dt = datetime.fromisoformat(until.replace('Z', '+00:00'))
            else:
                end_dt = datetime.now(timezone.utc)
        except (ValueError, AttributeError):
            # If parsing fails, return None range to indicate invalid dates
            return [(None, None)]

        # Calculate chunk duration
        total_duration = end_dt - start_dt
        chunk_duration = total_duration / chunks

        # Generate time ranges
        ranges = []
        for i in range(chunks):
            chunk_start = start_dt + (chunk_duration * i)
            chunk_end = start_dt + (chunk_duration * (i + 1))

            # Format as ISO strings
            ranges.append((
                chunk_start.isoformat().replace('+00:00', 'Z'),
                chunk_end.isoformat().replace('+00:00', 'Z')
            ))

        return ranges

    def get_active_unmerged_branches(
        self,
        owner: str,
        repo: str,
        days: int = 30,
        use_cache: bool = True,
    ) -> List[str]:
        """
        Get branches that were updated recently and have unmerged commits.
        Uses a single efficient query combining branch listing and comparison.

        Args:
            days: Consider branches updated in last N days

        Returns:
            List of branch names with unmerged commits
        """
        from datetime import datetime, timedelta, timezone

        # Get default branch first
        repo_info = self.get_with_cache(
            f"https://api.github.com/repos/{owner}/{repo}",
            use_cache=use_cache
        )
        default_branch = repo_info.get("default_branch", "main") if repo_info else "main"

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff_date.isoformat()

        # Single GraphQL query to get branches with commit info
        query = """
        query($owner: String!, $name: String!, $cursor: String) {
          repository(owner: $owner, name: $name) {
            refs(refPrefix: "refs/heads/", first: 100, after: $cursor, orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) {
              pageInfo { hasNextPage endCursor }
              nodes {
                name
                target {
                  ... on Commit {
                    oid
                    committedDate
                  }
                }
              }
            }
          }
          rateLimit { remaining resetAt limit cost }
        }
        """

        active_branches = []
        cursor = None

        while True:
            variables = {"owner": owner, "name": repo, "cursor": cursor}
            data = self.graphql(query, variables, use_cache=use_cache)
            if not data:
                break

            repo_data = data.get("data", {}).get("repository")
            if not repo_data:
                break

            refs = repo_data.get("refs", {})
            nodes = refs.get("nodes", [])

            # Filter by date and exclude default branch
            for node in nodes:
                branch_name = node.get("name")
                if branch_name == default_branch:
                    continue

                # Skip gh-pages and similar branches
                if branch_name and branch_name.startswith("gh-pages"):
                    continue

                target = node.get("target", {})
                commit_date = target.get("committedDate")

                if commit_date:
                    commit_dt = datetime.fromisoformat(commit_date.replace('Z', '+00:00'))
                    if commit_dt >= cutoff_date:
                        active_branches.append(branch_name)

            page_info = refs.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        if not active_branches:
            return []

        # Batch check which branches have unmerged commits (max 10 at a time to avoid rate limits)
        print(f"  Found {len(active_branches)} active branches (gh-pages excluded), checking merge status...")
        unmerged_branches = []
        batch_size = 10

        for i in range(0, len(active_branches), batch_size):
            batch = active_branches[i:i+batch_size]
            for branch in batch:
                # Use REST API compare endpoint (more efficient than GraphQL for this)
                compare_url = f"https://api.github.com/repos/{owner}/{repo}/compare/{default_branch}...{branch}"
                compare_data = self.get_with_cache(compare_url, use_cache=use_cache)

                if compare_data and isinstance(compare_data, dict):
                    ahead_by = compare_data.get("ahead_by", 0)
                    if ahead_by > 0:
                        unmerged_branches.append(branch)
                        print(f"    {branch}: {ahead_by} commits ahead")

            # Small delay between batches to respect rate limits
            if i + batch_size < len(active_branches):
                time.sleep(1)

        return unmerged_branches

    def _fetch_with_thread_id(self, owner: str, repo: str, sha: str, use_cache: bool) -> Dict[str, Any]:
        """Helper function to fetch commit details with thread identification."""
        thread_id = threading.get_ident() % 1000  # Use last 3 digits for readability
        data, headers = self.get_with_cache(
            f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}",
            use_cache,
            return_headers=True,
            silent=True  # Don't log individual requests
        )
        return {'data': data, 'thread_id': thread_id, 'headers': headers}

    def _fetch_rest_commit_details_parallel(self, commits_list: List[Dict], owner: str, repo: str, use_cache: bool, max_workers: int = 5) -> List[Dict[str, Any]]:
        """
        Fetch commit details in parallel with conservative settings.

        Args:
            commits_list: List of commit objects with 'sha' field
            owner: Repository owner
            repo: Repository name
            use_cache: Whether to use cache
            max_workers: Maximum parallel requests (default: 5)

        Returns:
            List of processed commits with stats
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        processed_commits = []
        batch_size = 10  # Process in small batches
        thread_id_map = {}  # Map real thread IDs to sequential worker numbers

        # Create executor once and reuse across all batches
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for batch_idx in range(0, len(commits_list), batch_size):
                batch = commits_list[batch_idx:batch_idx+batch_size]
                batch_headers = []  # Collect headers from this batch

                # Submit all requests in this batch with worker tracking
                future_to_data = {}
                for c in batch:
                    future = executor.submit(
                        self._fetch_with_thread_id,
                        owner,
                        repo,
                        c['sha'],
                        use_cache
                    )
                    future_to_data[future] = c

                # Collect results as they complete
                for future in as_completed(future_to_data):
                    rest_commit = future_to_data[future]
                    sha = rest_commit.get('sha')

                    try:
                        result = future.result(timeout=40)
                        commit_details = result['data']
                        thread_id = result['thread_id']
                        headers = result['headers']

                        # Collect headers for batch summary
                        if headers:
                            batch_headers.append(headers)

                        # Map thread ID to sequential worker number (1, 2, 3, ...)
                        if thread_id not in thread_id_map:
                            thread_id_map[thread_id] = len(thread_id_map) + 1
                        worker_num = thread_id_map[thread_id]

                        if commit_details:
                            stats = commit_details.get('stats', {})
                            additions = stats.get('additions', 0)
                            deletions = stats.get('deletions', 0)

                            commit_payload = rest_commit.get('commit', {}) or {}
                            raw_author = commit_payload.get('author') or {}
                            raw_committer = commit_payload.get('committer') or {}

                            # The GitHub account, present only when the commit
                            # is linked to one: an unlinked commit has
                            # `author: null`. The git `name` is not a login —
                            # standing in for one fabricates an account that
                            # never existed, and dropping `email` leaves the
                            # commit unattributable (#203).
                            account = rest_commit.get('author')
                            if not isinstance(account, dict):
                                account = {}

                            print(f"[REST][Worker-{worker_num}] Fetched {sha[:8]}: +{additions}/-{deletions}")

                            processed_commits.append({
                                'oid': sha,
                                'message': commit_payload.get('message', ''),
                                'messageHeadline': commit_payload.get('message', '').split('\n')[0],
                                'committedDate': raw_author.get('date'),
                                # The node shape the GraphQL query returns
                                # (`author { name email user { login
                                # databaseId } }`), so the GraphQL->REST
                                # mapping in bronze/commits.py treats both
                                # paths identically. REST's `author.id` is
                                # GraphQL's `user.databaseId`.
                                'author': {
                                    'name': raw_author.get('name'),
                                    'email': raw_author.get('email'),
                                    'date': raw_author.get('date'),
                                    'user': {
                                        'login': account.get('login'),
                                        'databaseId': account.get('id'),
                                    },
                                },
                                'committer': {
                                    'name': raw_committer.get('name'),
                                    'email': raw_committer.get('email'),
                                    'date': raw_committer.get('date'),
                                },
                                'parents': {
                                    'nodes': [
                                        {'oid': p.get('sha')}
                                        for p in (rest_commit.get('parents') or [])
                                        if isinstance(p, dict) and p.get('sha')
                                    ]
                                },
                                'additions': additions,
                                'deletions': deletions,
                            })
                    except OfflineCacheMiss:
                        # An offline replay miss must stop the run; swallowing
                        # it here would drop the commit and report a partial
                        # answer (#199).
                        raise
                    except Exception as e:
                        print(f"[REST][Worker-?][WARN] Failed {sha[:8] if sha else 'unknown'}: {e}")

                # Show rate limit summary for this batch
                if batch_headers:
                    last_header = batch_headers[-1]  # Use most recent
                    remaining = last_header.get('X-RateLimit-Remaining', 'Unknown')
                    limit = last_header.get('X-RateLimit-Limit', 'Unknown')
                    reset_time = last_header.get('X-RateLimit-Reset', 'Unknown')
                    if reset_time != 'Unknown':
                        reset_datetime = datetime.fromtimestamp(int(reset_time))
                        print(f"[REST][Batch {batch_idx//batch_size + 1}] Rate limit: {remaining}/{limit}, resets at {reset_datetime}")
                    else:
                        print(f"[REST][Batch {batch_idx//batch_size + 1}] Rate limit: {remaining}/{limit}")

                # Small delay between batches
                if batch_idx + batch_size < len(commits_list):
                    time.sleep(0.3)

        return processed_commits

    def graphql_commit_history(
        self,
        owner: str,
        repo: str,
        page_size: int,
        max_pages: Optional[int] = None,
        max_commits: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        use_cache: bool = True,
        branches: Optional[List[str]] = None,
        split_large_extractions: bool = True,
        time_chunks: int = 3,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Fetch commit history with automatic REST fallback on GraphQL failures.

        Features:
        - Switches to REST after 16s timeout (2 failed attempts)
        - REST fallback extracts 50 commits, then retries GraphQL
        - Circuit breaker for repeated failures

        Args:
            branches: List of branch names to extract from. If None, uses default branch.
            split_large_extractions: If True, splits extraction into time chunks
            time_chunks: Number of time periods to split extraction into (default: 3)

        Returns:
            Tuple of (commits list, rate limit metadata)
        """
        commits_by_sha: Dict[str, Dict[str, Any]] = {}  # Deduplicate by SHA
        rate_meta: Dict[str, Any] = {}
        graphql_failures = 0  # Track GraphQL failures
        using_rest_fallback = False
        rest_commit_count = 0
        rest_commits_before_retry = 50  # Try GraphQL again after 50 REST commits

        # Always include default branch, optionally add others
        branches_to_process = [None]  # None = default branch
        if branches:
            branches_to_process.extend(branches)
            print(f"  Processing {len(branches_to_process)} branches (main + {len(branches)} active)")

        # Determine if we should split by time
        time_ranges = [(since, until)]  # Default: single time range

        if split_large_extractions and (since or until):
            time_ranges = self._split_time_range(since, until, chunks=time_chunks)
            print(f"  Splitting extraction into {len(time_ranges)} time periods to avoid API overload")

        for branch in branches_to_process:
            branch_name = branch if branch else "default branch"
            print(f"    Extracting from: {branch_name}")

            # Process each time range for this branch
            for time_idx, (range_since, range_until) in enumerate(time_ranges):
                if len(time_ranges) > 1:
                    print(f"      Time period {time_idx + 1}/{len(time_ranges)}: {range_since} to {range_until}")

                if branch:
                    query = """
                    query($owner: String!, $name: String!, $branch: String!, $pageSize: Int!, $cursor: String, $since: GitTimestamp, $until: GitTimestamp) {
                      repository(owner: $owner, name: $name) {
                        ref(qualifiedName: $branch) {
                          target {
                            ... on Commit {
                              history(first: $pageSize, after: $cursor, since: $since, until: $until) {
                                pageInfo { hasNextPage endCursor }
                                nodes {
                                  oid
                                  message
                                  messageHeadline
                                  committedDate
                                  author { name email user { login databaseId } }
                                  committer { name email date user { login databaseId } }
                                  additions
                                  deletions
                                  parents(first: 100) { nodes { oid } }
                                }
                              }
                            }
                          }
                        }
                      }
                      rateLimit { remaining resetAt limit cost }
                    }
                    """
                else:
                    query = """
                    query($owner: String!, $name: String!, $pageSize: Int!, $cursor: String, $since: GitTimestamp, $until: GitTimestamp) {
                      repository(owner: $owner, name: $name) {
                        defaultBranchRef {
                          name
                          target {
                            ... on Commit {
                              history(first: $pageSize, after: $cursor, since: $since, until: $until) {
                                pageInfo { hasNextPage endCursor }
                                nodes {
                                  oid
                                  message
                                  messageHeadline
                                  committedDate
                                  author { name email user { login databaseId } }
                                  committer { name email date user { login databaseId } }
                                  additions
                                  deletions
                                  parents(first: 100) { nodes { oid } }
                                }
                              }
                            }
                          }
                        }
                      }
                      rateLimit { remaining resetAt limit cost }
                    }
                    """

                cursor: Optional[str] = None
                pages = 0
                period_commits = 0
                rest_page = 1
                last_rest_commit_sha = None

                while True:
                    if max_pages is not None and pages >= max_pages:
                        break
                    if max_commits is not None and len(commits_by_sha) >= max_commits:
                        break

                    # CIRCUIT BREAKER: Switch to REST after 1 GraphQL failure (30s timeout)
                    if graphql_failures >= 1 and not using_rest_fallback:
                        print(f"[CIRCUIT BREAKER] GraphQL failed (30s timeout)")
                        print(f"        Switching to REST API fallback...")
                        using_rest_fallback = True
                        rest_commit_count = 0
                        graphql_failures = 0

                    # REST FALLBACK
                    if using_rest_fallback:

                        rest_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
                        params = []
                        if branch:
                            params.append(f"sha={branch}")
                        if range_since:
                            params.append(f"since={range_since}")
                        if range_until:
                            params.append(f"until={range_until}")
                        params.append(f"per_page=50")
                        params.append(f"page={rest_page}")

                        rest_url = f"{rest_url}?{'&'.join(params)}"

                        rest_data = self.get_with_cache(rest_url, use_cache=use_cache, silent=True)

                        if not rest_data or not isinstance(rest_data, list):
                            print(f"[REST] Page {rest_page}: No more commits available")
                            break

                        # Filter commits that haven't been processed yet
                        new_commits = [
                            c for c in rest_data
                            if c.get('sha') and c.get('sha') not in commits_by_sha
                        ]

                        total_in_page = len(rest_data)
                        already_processed = total_in_page - len(new_commits)

                        if not new_commits:
                            print(f"[REST] Page {rest_page}: Found {total_in_page} commits, all already in dataset (skipping)")
                            rest_page += 1
                            continue

                        print(f"[REST] Page {rest_page}: Found {total_in_page} commits, {already_processed} already in dataset (processing {len(new_commits)} new)")

                        # Fetch commit details in parallel
                        processed = self._fetch_rest_commit_details_parallel(
                            new_commits, owner, repo, use_cache, max_workers=5
                        )

                        # Add to commits dictionary
                        for commit in processed:
                            sha = commit.get('oid')
                            if sha and sha not in commits_by_sha:
                                commits_by_sha[sha] = commit
                                rest_commit_count += 1
                                period_commits += 1
                                last_rest_commit_sha = sha

                        rest_page += 1

                        # Check if we should retry GraphQL
                        if rest_commit_count >= rest_commits_before_retry:
                            if last_rest_commit_sha:
                                print(f"[REST->GRAPHQL] Extracted {rest_commit_count} commits via REST (last: {last_rest_commit_sha[:8]}...)")
                                print(f"[REST->GRAPHQL] GraphQL will continue from cursor position (deduplication prevents reprocessing)")
                            else:
                                print(f"[REST->GRAPHQL] Extracted {rest_commit_count} commits via REST. Retrying GraphQL...")
                            using_rest_fallback = False
                            rest_commit_count = 0
                            graphql_failures = 0
                            time.sleep(1)
                            continue

                        # If REST returned less than 100 commits, we're done
                        if len(rest_data) < 100:
                            print(f"[REST] Reached end of commits")
                            break

                        time.sleep(1)  # Rate limit protection for REST
                        continue

                    # GRAPHQL MODE
                    variables = {
                        "owner": owner,
                        "name": repo,
                        "pageSize": page_size,
                        "cursor": cursor,
                        "since": range_since,
                        "until": range_until,
                    }
                    if branch:
                        variables["branch"] = f"refs/heads/{branch}"


                    data = self.graphql(query, variables, use_cache=use_cache, timeout=30)

                    if not data:
                        graphql_failures += 1
                        continue  # Circuit breaker will activate REST fallback after 1 failure

                    repo_data = data.get("data", {}).get("repository")
                    rate_meta = data.get("data", {}).get("rateLimit", {}) or {}

                    # Success! Reset failure counter
                    graphql_failures = 0

                    if not repo_data:
                        break

                    # Extract history based on query type
                    if branch:
                        ref_data = repo_data.get("ref")
                        if not ref_data:
                            print(f"[GRAPHQL] Branch '{branch}' not found")
                            break
                        target = ref_data.get("target", {})
                    else:
                        default_ref = repo_data.get("defaultBranchRef")
                        if not default_ref:
                            break
                        target = default_ref.get("target", {})

                    history = target.get("history") if isinstance(target, dict) else None
                    if not history:
                        break

                    nodes = history.get("nodes", [])

                    # Add commits to dict (auto-deduplicates by SHA)
                    for node in nodes:
                        sha = node.get('oid')
                        if sha and sha not in commits_by_sha:
                            # Set additions/deletions to 0 if unavailable (SERVICE_UNAVAILABLE errors)
                            if node.get('additions') is None:
                                node['additions'] = 0
                            if node.get('deletions') is None:
                                node['deletions'] = 0

                            commits_by_sha[sha] = node
                            period_commits += 1

                    page_info = history.get("pageInfo", {})
                    has_next = page_info.get("hasNextPage")
                    cursor = page_info.get("endCursor")
                    pages += 1

                    # Log rate limit after processing commits
                    if rate_meta:
                        remaining = rate_meta.get("remaining", 0)
                        limit = rate_meta.get("limit", 5000)
                        print(f"[GRAPHQL] Rate limit: {remaining}/{limit}, {len(commits_by_sha)} commits processed")
                        if remaining < 100:
                            print(f"[GRAPHQL][WARN] Rate limit low ({remaining}). Pausing 30s...")
                            time.sleep(30)

                    # Delay between pages
                    if has_next:
                        time.sleep(1)

                    if not has_next:
                        break

                if len(time_ranges) > 1 and period_commits > 0:
                    print(f"[GRAPHQL] Extracted {period_commits} total unique commits from this period")

        commits = list(commits_by_sha.values())
        if branches:
            print(f"  [GRAPHQL] Total unique commits across all branches: {len(commits)}")
        return commits, rate_meta

    # ============================================================================
    # REPOSITORY STRUCTURE EXTRACTION (REST + GraphQL Fallback)
    # ============================================================================

    def get_repository_tree(
        self,
        owner: str,
        repo: str,
        branch: str = "main",
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Get file tree using REST API Git Trees as primary method.
        If the tree is truncated, automatically falls back to GraphQL.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch to analyze (default: "main")
            use_cache: Whether to use cache

        Returns:
            Dictionary with the standardized file tree
        """
        logger = logging.getLogger(__name__)

        try:
            # Step 1: Get branch SHA via REST
            branch_url = f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}"
            branch_data = self.get_with_cache(branch_url, use_cache=use_cache)

            if not branch_data:
                logger.error(f"Branch {branch} not found for {owner}/{repo}")
                return self._empty_tree_response(owner, repo, branch, error="Branch not found")

            tree_sha = branch_data['commit']['sha']
            logger.info(f"Fetching tree for {owner}/{repo} (SHA: {tree_sha[:8]})")

            # Step 2: Get recursive tree with REST (1 request!)
            tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree_sha}"
            tree_data = self.get_with_cache(
                f"{tree_url}?recursive=1",
                use_cache=use_cache
            )

            if not tree_data:
                logger.error(f"Failed to fetch tree for {owner}/{repo}")
                return self._empty_tree_response(owner, repo, branch, error="Tree fetch failed")

            is_truncated = tree_data.get('truncated', False)
            raw_tree = tree_data.get('tree', [])

            logger.info(f"  Items fetched: {len(raw_tree)}")
            logger.info(f"  Truncated: {is_truncated}")

            # If truncated, fallback to GraphQL
            if is_truncated:
                logger.warning(f"  Tree truncated! Falling back to GraphQL...")
                return self.graphql_repository_tree(owner, repo, branch, use_cache)

            # Step 3: Standardize node format
            standardized_tree = []
            for item in raw_tree:
                node = self._standardize_tree_node(item)
                if node:
                    standardized_tree.append(node)

            return {
                'owner': owner,
                'repository': repo,
                'branch': branch,
                'sha': tree_sha,
                'tree': standardized_tree,
                'truncated': is_truncated,
                'extracted_at': datetime.now(timezone.utc).isoformat(),
                'method': 'rest',
                'total_items': len(standardized_tree)
            }

        except OfflineCacheMiss:
            # Must not become an "empty tree" result: an offline replay miss
            # stops the run (#199).
            raise
        except Exception as e:
            logger.error(f"Error in get_repository_tree: {str(e)}")
            return self._empty_tree_response(owner, repo, branch, error=str(e))

    def graphql_repository_tree(
        self,
        owner: str,
        repo: str,
        branch: str = "main",
        use_cache: bool = True,
        max_depth: int = 100
    ) -> Dict[str, Any]:
        """
        Extract full tree using GraphQL (used when REST truncates).
        Iterative implementation with stack to avoid deep recursion.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch to analyze
            use_cache: Whether to use cache
            max_depth: Maximum depth for safety

        Returns:
            Dictionary with hierarchical tree
        """
        logger = logging.getLogger(__name__)

        def build_tree_iterative(start_path: str = "") -> List[Dict[str, Any]]:
            """Build tree using iterative stack."""
            root_tree = []
            stack = [(start_path, root_tree)]
            processed = set()

            while stack and len(processed) < max_depth:
                current_path, parent_list = stack.pop()

                if current_path in processed:
                    logger.warning(f"Skipping already processed path: {current_path}")
                    continue
                processed.add(current_path)

                expression = f"{branch}:{current_path}" if current_path else f"{branch}:"

                query = """
                query($owner: String!, $repo: String!, $expression: String!) {
                  repository(owner: $owner, name: $repo) {
                    object(expression: $expression) {
                      ... on Tree {
                        entries {
                          name
                          type
                          mode
                          path
                          extension
                          object {
                            ... on Blob {
                              byteSize
                              isBinary
                              oid
                            }
                          }
                        }
                      }
                    }
                  }
                }
                """

                variables = {
                    "owner": owner,
                    "repo": repo,
                    "expression": expression
                }

                try:
                    result = self.graphql(query, variables, use_cache=use_cache)

                    if not result or 'data' not in result:
                        logger.warning(f"No data returned for path: {current_path}")
                        continue

                    repo_obj = result.get('data', {}).get('repository', {})
                    if not repo_obj:
                        logger.warning(f"Repository not found")
                        continue

                    tree_obj = repo_obj.get('object', {})
                    if not tree_obj:
                        logger.debug(f"No tree object for path: {current_path}")
                        continue

                    entries = tree_obj.get('entries', [])

                    for entry in entries:
                        entry_type = entry.get('type')
                        entry_name = entry.get('name')
                        entry_path = entry.get('path')

                        if entry_type == 'tree':
                            logger.debug(f"Processing directory: {entry_path}")
                            directory_node = {
                                'name': entry_name,
                                'path': entry_path,
                                'type': 'directory',
                                'children': []
                            }
                            parent_list.append(directory_node)
                            stack.append((entry_path, directory_node['children']))

                        elif entry_type == 'blob':
                            blob_info = entry.get('object', {})
                            file_node = {
                                'name': entry_name,
                                'path': entry_path,
                                'type': 'file',
                                'extension': entry.get('extension', ''),
                                'size': blob_info.get('byteSize', 0),
                                'is_binary': blob_info.get('isBinary', False),
                                'oid': blob_info.get('oid', '')
                            }
                            parent_list.append(file_node)

                except OfflineCacheMiss:
                    # Must not be skipped as a "failed path": an offline
                    # replay miss stops the run (#199).
                    raise
                except Exception as e:
                    logger.error(f"Error processing path {current_path}: {str(e)}")
                    continue

            return root_tree

        logger.info(f"Building repository tree via GraphQL for {owner}/{repo}")

        try:
            tree = build_tree_iterative("")

            return {
                'owner': owner,
                'repository': repo,
                'branch': branch,
                'tree': tree,
                'extracted_at': datetime.now(timezone.utc).isoformat(),
                'method': 'graphql',
                'total_items': len(tree)
            }
        except OfflineCacheMiss:
            # Must not become an "empty tree" error payload: an offline
            # replay miss stops the run (#199).
            raise
        except Exception as e:
            logger.error(f"Failed to build repository tree: {str(e)}")
            return {
                'owner': owner,
                'repository': repo,
                'branch': branch,
                'tree': [],
                'error': str(e),
                'extracted_at': datetime.now(timezone.utc).isoformat(),
                'method': 'graphql'
            }

    def _standardize_tree_node(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Standardize the format of a tree node (REST or GraphQL).

        Args:
            item: Raw API item

        Returns:
            Standardized node or None if invalid
        """
        node_type = item.get('type', '')
        path = item.get('path', '')

        if not path:
            return None

        name = path.split('/')[-1] if '/' in path else path

        if node_type == 'blob':
            file_type = 'file'
        elif node_type == 'tree':
            file_type = 'directory'
        else:
            file_type = node_type

        standardized = {
            'name': name,
            'path': path,
            'type': file_type,
            'sha': item.get('sha', item.get('oid', '')),
            'mode': item.get('mode', '')
        }

        if file_type == 'file':
            extension = ''
            if '.' in name:
                extension = '.' + name.rsplit('.', 1)[-1]

            standardized['extension'] = extension

            size = item.get('size')
            if size is None and 'object' in item:
                size = item['object'].get('byteSize', 0)

            standardized['size'] = size or 0
            standardized['is_binary'] = item.get('is_binary', False)

        elif file_type == 'directory':
            standardized['children'] = item.get('children', [])

        return standardized

    def _empty_tree_response(
        self,
        owner: str,
        repo: str,
        branch: str,
        error: str = ""
    ) -> Dict[str, Any]:
        """
        Return empty structure on error.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch
            error: Error message

        Returns:
            Standardized empty structure
        """
        return {
            'owner': owner,
            'repository': repo,
            'branch': branch,
            'sha': '',
            'tree': [],
            'truncated': False,
            'extracted_at': datetime.now(timezone.utc).isoformat(),
            'method': 'rest',
            'total_items': 0,
            'error': error
        }

    def get_paginated(
        self,
        base_url: str,
        use_cache: bool = True,
        per_page: int = 50,
        start_page: int = 1,
        max_pages: Optional[int] = None,
    ) -> List[Any]:
        """
        Fetch all pages for list endpoints that support per_page & page params.
        Stops when a page returns fewer than per_page results or when max_pages is reached.
        """
        results: List[Any] = []
        page = start_page
        while True:
            if max_pages is not None and page > max_pages:
                break
            sep = '&' if ('?' in base_url) else '?'
            url = f"{base_url}{sep}per_page={per_page}&page={page}"
            data = self.get_with_cache(url, use_cache)
            if data is None:
                if page > start_page:
                    print(f"[WARN] Stopped paginating {base_url} at page {page}: "
                          f"returning the {len(results)} items fetched so far")
                break
            if isinstance(data, list):
                results.extend(data)
                if len(data) < per_page:
                    break
            else:
                # Non-list response; stop paging
                break
            page += 1
        return results

    def _log_rate_limit(self, response: requests.Response, prefix: str = "REST") -> None:
        """Log rate limit information from response headers."""
        remaining = response.headers.get('X-RateLimit-Remaining', 'Unknown')
        limit = response.headers.get('X-RateLimit-Limit', 'Unknown')
        reset_time = response.headers.get('X-RateLimit-Reset', 'Unknown')

        if reset_time != 'Unknown':
            reset_datetime = datetime.fromtimestamp(int(reset_time))
            print(f"[{prefix}] Rate limit: {remaining}/{limit}, resets at {reset_datetime}")
        else:
            print(f"[{prefix}] Rate limit: {remaining}/{limit}")

def save_json_data(data: Any, filepath: str, timestamp: bool = True) -> str:
    """Save data to JSON file with optional timestamp metadata."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    if timestamp:
        now = datetime.now(timezone.utc).isoformat()
        if isinstance(data, dict):
            # Copy: callers may reuse the dict (e.g. in a consolidated file).
            data = {**data, '_metadata': {
                'extracted_at': now,
                'file_path': filepath
            }}
        elif isinstance(data, list) and len(data) > 0:

            metadata = {
                '_metadata': {
                    'extracted_at': now,
                    'file_path': filepath,
                    'record_count': len(data)
                }
            }
            data = [metadata] + data

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Saved data to: {filepath}")
    return filepath

def load_json_data(filepath: str) -> Any:
    """Load data from JSON file."""
    if not os.path.exists(filepath):
        return None

    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def update_data_registry(layer: str, entity: str, files: List[str]) -> None:
    """Update the data registry with new files."""
    registry_path = f"data/{layer}/registry.json"

    registry = load_json_data(registry_path) or {}

    if entity not in registry:
        registry[entity] = {}

    registry[entity]['files'] = files
    registry[entity]['updated_at'] = datetime.now(timezone.utc).isoformat()
    registry[entity]['layer'] = layer

    save_json_data(registry, registry_path, timestamp=False)

class OrganizationConfig:
    """Configuration for organization data extraction."""

    def __init__(self, org_name: str):
        self.org_name = org_name
        # CoOps-specific blacklist for repositories to skip
        self.repo_blacklist: List[str] = [
            "Hi.Events",
            "Qualifying-Software-Engineers-Undergraduates-in-DevOps"
        ]

    def should_skip_repo(self, repo: Dict[str, Any]) -> bool:
        """Check if repository should be skipped (blacklisted or fork)."""
        return (
            repo.get('name') in self.repo_blacklist or
            repo.get('fork', False)
        )


def parse_github_date(date_str: str) -> Optional[datetime]:
    """
    Parse GitHub API date strings in various formats.
    Handles both UTC (Z) and timezone offset formats.

    Args:
        date_str: Date string from GitHub API

    Returns:
        datetime object or None if parsing fails
    """
    if not date_str:
        return None

    date_str = str(date_str).strip()

    # Try multiple date formats that GitHub uses
    formats = [
        '%Y-%m-%dT%H:%M:%SZ',  # UTC format: 2025-09-21T17:13:42Z
        '%Y-%m-%dT%H:%M:%S',    # Without timezone
    ]

    # Handle ISO 8601 format with timezone offset (e.g., 2025-09-21T17:13:42-03:00)
    if '+' in date_str or (date_str.count('-') > 2):  # Check for timezone offset
        try:
            # Extract the base datetime part (YYYY-MM-DDTHH:MM:SS)
            # Remove timezone suffix like -03:00 or +05:30
            base_date_str = date_str[:19]  # First 19 chars: YYYY-MM-DDTHH:MM:SS
            return datetime.strptime(base_date_str, '%Y-%m-%dT%H:%M:%S')
        except (ValueError, IndexError):
            pass

    # Try standard formats
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # If all parsing fails, return None
    return None
