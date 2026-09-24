/**
 * Data Source Service
 *
 * Handles fetching data from local or remote GitHub sources
 */

// Use local data in development if VITE_USE_LOCAL_DATA is true
const USE_LOCAL_DATA = import.meta.env.VITE_USE_LOCAL_DATA === 'true';

// GitHub repository from environment variables. The organization is deliberately
// NOT defaulted: an unset VITE_GITHUB_ORG must fail closed (see
// DataUnconfiguredError) instead of silently reading a third party's data.
const GITHUB_REPO = import.meta.env.VITE_GITHUB_REPO || 'CoOps';

/**
 * Thrown by {@link fetchData} when the dashboard is not configured to read from
 * any GitHub organization (`VITE_GITHUB_ORG` is unset and remote mode is used).
 *
 * Sibling of {@link DataNotFoundError}, but a different condition: a missing
 * file means the pipeline has not produced it yet; this means there is no URL
 * to fetch from at all, so no request is attempted and the UI shows a
 * "configure VITE_GITHUB_ORG" state instead of fabricating a plausible answer.
 */
export class DataUnconfiguredError extends Error {
  constructor() {
    super(
      'VITE_GITHUB_ORG is not set. Configure it to point at the GitHub organization whose data this dashboard should read.'
    );
    this.name = 'DataUnconfiguredError';
  }
}

export function isDataUnconfiguredError(error: unknown): error is DataUnconfiguredError {
  return error instanceof DataUnconfiguredError;
}

/**
 * Get the base URL for data fetching
 * - Local mode: /data (expects data in public/data during development)
 * - Remote mode: Fetches from GitHub raw content URL
 * @throws {DataUnconfiguredError} when remote mode is used without VITE_GITHUB_ORG
 */
export function getDataBasePath(): string {
  if (USE_LOCAL_DATA) {
    return '/data';
  }
  const org = import.meta.env.VITE_GITHUB_ORG;
  if (!org) {
    throw new DataUnconfiguredError();
  }
  return `https://raw.githubusercontent.com/${org}/${GITHUB_REPO}/main/data`;
}

/**
 * Thrown by {@link fetchData} when a data file does not exist (HTTP 404).
 *
 * A missing file means the pipeline hasn't generated it yet (e.g. a fresh fork
 * or a first pipeline run still in progress), not a failure, so pages render an
 * explanatory empty state for it instead of an error. Network errors, other
 * HTTP statuses and invalid JSON keep throwing regular errors.
 */
export class DataNotFoundError extends Error {
  /** Path relative to the data directory, e.g. 'silver/temporal_events.json'. */
  readonly path: string;
  readonly url: string;

  constructor(path: string, url: string) {
    super(`Data file not found: ${url} (status: 404)`);
    this.name = 'DataNotFoundError';
    this.path = path;
    this.url = url;
  }
}

export function isDataNotFoundError(error: unknown): error is DataNotFoundError {
  return error instanceof DataNotFoundError;
}

/**
 * Fetch JSON data from the configured source
 * @throws {DataNotFoundError} when the file does not exist (HTTP 404)
 * @param path - Relative path to the JSON file (e.g., 'silver/members_analytics.json')
 */
export async function fetchData<T = any>(path: string): Promise<T> {
  const basePath = getDataBasePath();
  const url = `${basePath}/${path}`;

  try {
    const response = await fetch(url);
    if (response.status === 404) {
      throw new DataNotFoundError(path, url);
    }
    if (!response.ok) {
      // statusText is empty over HTTP/2 (e.g. raw.githubusercontent.com), so
      // always include the numeric status code.
      const statusText = response.statusText ? ` ${response.statusText}` : '';
      throw new Error(`Failed to fetch ${url} (status: ${response.status}${statusText})`);
    }
    return await response.json();
  } catch (error) {
    if (isDataNotFoundError(error)) {
      console.warn(`Data file not generated yet: ${url}`);
    } else {
      console.error(`Error fetching data from ${url}:`, error);
    }
    throw error;
  }
}

/**
 * Fetch the repository names listed in `silver/available_repos.json`
 * (a JSON array of strings written by the pipeline).
 *
 * Metadata entries and non-string values are ignored. Errors (including a
 * missing file) propagate to the caller.
 */
export async function fetchAvailableRepoNames(): Promise<string[]> {
  const data = await fetchData<unknown>('silver/available_repos.json');
  if (!Array.isArray(data)) return [];
  return filterMetadata(data).filter((name): name is string => typeof name === 'string');
}

/**
 * Filter out metadata entries (and null/undefined entries) from data arrays
 */
export function filterMetadata<T>(data: T[]): T[] {
  return data.filter((item: any) => item != null && !item._metadata);
}
