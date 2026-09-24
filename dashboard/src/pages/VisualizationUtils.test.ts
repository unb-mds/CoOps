import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import type { LanguageAnalysis } from './VisualizationUtils';

type UtilsModule = typeof import('./VisualizationUtils');
type DataSourceModule = typeof import('../services/dataSource');

const repoA: LanguageAnalysis = {
  repository: 'repo-a',
  owner: 'org',
  branch: 'main',
  total_files: 3,
  total_bytes: 300,
  languages: [{ language: 'Python', file_count: 3, total_bytes: 300, percentage: 100 }],
};

const repoB: LanguageAnalysis = {
  repository: 'repo-b',
  owner: 'org',
  branch: 'dev',
  total_files: 1,
  total_bytes: 10,
  languages: [],
};

const dataUrl = (path: string) =>
  `https://raw.githubusercontent.com/${import.meta.env.VITE_GITHUB_ORG}/${
    import.meta.env.VITE_GITHUB_REPO || 'CoOps'
  }/main/data/${path}`;

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

const fetchMock = vi.fn<(input: string) => Promise<Response>>();

let dataSource: DataSourceModule;

// The class keeps a static cache, so load a fresh module for every test
async function loadUtils(): Promise<UtilsModule['VisualizationUtils']> {
  vi.resetModules();
  const mod: UtilsModule = await import('./VisualizationUtils');
  // same module instance VisualizationUtils uses (for instanceof checks)
  dataSource = await import('../services/dataSource');
  return mod.VisualizationUtils;
}

describe('VisualizationUtils', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    vi.stubEnv('VITE_GITHUB_ORG', 'acme-org');
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  describe('fetchAvailableRepos', () => {
    test('returns repository names, skipping metadata/invalid entries', async () => {
      fetchMock.mockResolvedValue(
        jsonResponse([
          { _metadata: { generated_at: 'now' } },
          { repository: 'no-langs' },
          { languages: [] },
          repoA,
          repoB,
        ])
      );
      const Utils = await loadUtils();
      await expect(Utils.fetchAvailableRepos()).resolves.toEqual(['repo-a', 'repo-b']);
      // goes through dataSource (raw.githubusercontent.com or /data), not BASE_URL
      expect(fetchMock).toHaveBeenCalledWith(
        `${dataSource.getDataBasePath()}/silver/language_analysis_all.json`
      );
      expect(fetchMock).not.toHaveBeenCalledWith(
        `${import.meta.env.BASE_URL}data/silver/language_analysis_all.json`
      );
    });

    test('resolves to the configured data URL', async () => {
      fetchMock.mockResolvedValue(jsonResponse([repoA]));
      const Utils = await loadUtils();
      await Utils.fetchAvailableRepos();
      const expected = import.meta.env.VITE_USE_LOCAL_DATA === 'true'
        ? '/data/silver/language_analysis_all.json'
        : dataUrl('silver/language_analysis_all.json');
      expect(fetchMock).toHaveBeenCalledWith(expected);
    });

    test('keeps analysis records that carry their own _metadata key', async () => {
      // Real pipeline output: every record has a _metadata key, not only the header entry
      fetchMock.mockResolvedValue(
        jsonResponse([
          { _metadata: { extracted_at: 'now', record_count: 2 } },
          { _metadata: { extracted_at: 'now' }, ...repoA },
          { _metadata: { extracted_at: 'now' }, ...repoB },
        ])
      );
      const Utils = await loadUtils();
      await expect(Utils.fetchAvailableRepos()).resolves.toEqual(['repo-a', 'repo-b']);
      await expect(Utils.fetchLanguageData('repo-a')).resolves.toMatchObject(repoA);
    });

    test('uses the cache after the first successful load', async () => {
      fetchMock.mockResolvedValue(jsonResponse([repoA]));
      const Utils = await loadUtils();
      await Utils.fetchAvailableRepos();
      await Utils.fetchAvailableRepos();
      await Utils.fetchLanguageData('repo-a');
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    test('returns empty list when payload is not an array', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ repository: 'repo-a', languages: [] }));
      const Utils = await loadUtils();
      await expect(Utils.fetchAvailableRepos()).resolves.toEqual([]);
    });

    test('returns empty list and logs on HTTP error, and retries on next call', async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(null, false, 500));
      fetchMock.mockResolvedValueOnce(jsonResponse([repoB]));
      const Utils = await loadUtils();
      await expect(Utils.fetchAvailableRepos()).resolves.toEqual([]);
      expect(console.error).toHaveBeenCalledWith(
        'Error loading language analysis data:',
        expect.objectContaining({ message: expect.stringContaining('(status: 500)') })
      );
      expect(console.error).toHaveBeenCalledWith('Error fetching repositories:', expect.any(Error));
      // Cache was not marked loaded, so a second call fetches again
      await expect(Utils.fetchAvailableRepos()).resolves.toEqual(['repo-b']);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });

  describe('fetchLanguageData', () => {
    test('returns the analysis for a known repository', async () => {
      fetchMock.mockResolvedValue(jsonResponse([repoA, repoB]));
      const Utils = await loadUtils();
      await expect(Utils.fetchLanguageData('repo-b')).resolves.toEqual(repoB);
    });

    test('returns null and warns for an unknown repository', async () => {
      fetchMock.mockResolvedValue(jsonResponse([repoA]));
      const Utils = await loadUtils();
      await expect(Utils.fetchLanguageData('missing')).resolves.toBeNull();
      expect(console.warn).toHaveBeenCalledWith('Repository "missing" not found in cache');
    });

    // #73: errors must reach the page instead of being reported as "no data"
    test('propagates network errors instead of returning null', async () => {
      fetchMock.mockRejectedValue(new Error('offline'));
      const Utils = await loadUtils();
      await expect(Utils.fetchLanguageData('repo-a')).rejects.toThrow('offline');
    });

    test('propagates HTTP errors with the status', async () => {
      fetchMock.mockResolvedValue(jsonResponse(null, false, 503));
      const Utils = await loadUtils();
      await expect(Utils.fetchLanguageData('repo-a')).rejects.toThrow('(status: 503)');
    });

    test('throws DataNotFoundError when the analysis file does not exist', async () => {
      fetchMock.mockResolvedValue(jsonResponse(null, false, 404));
      const Utils = await loadUtils();
      const error = await Utils.fetchLanguageData('repo-a').catch((e: unknown) => e);
      expect(dataSource.isDataNotFoundError(error)).toBe(true);
      expect((error as InstanceType<DataSourceModule['DataNotFoundError']>).path).toBe(
        'silver/language_analysis_all.json'
      );
      expect(console.error).not.toHaveBeenCalledWith(
        'Error loading language analysis data:',
        expect.anything()
      );
    });
  });

  describe('fetchTreeData', () => {
    // Output of convert_tree_to_hierarchy wrapped by the silver pipeline
    // (save_json_data adds a _metadata key next to the tree)
    const hierarchyFile = {
      repository: 'repo-a',
      owner: 'org',
      branch: 'main',
      extracted_at: '2024-01-01T00:00:00',
      hierarchy: {
        name: 'root',
        type: 'directory',
        children: [
          {
            name: 'src',
            type: 'directory',
            path: 'src',
            children: [
              {
                name: 'main.py',
                type: 'file',
                language: 'Python',
                size: 120,
                extension: '.py',
                path: 'src/main.py',
              },
            ],
          },
        ],
      },
      _metadata: { extracted_at: 'now', file_path: 'data/silver/hierarchy_repo-a.json' },
    };

    test('fetches silver/hierarchy_<repo>.json and returns the tree root', async () => {
      fetchMock.mockResolvedValue(jsonResponse(hierarchyFile));
      const Utils = await loadUtils();
      await expect(Utils.fetchTreeData('repo-a')).resolves.toEqual(hierarchyFile.hierarchy);
      expect(fetchMock).toHaveBeenCalledWith(
        `${dataSource.getDataBasePath()}/silver/hierarchy_repo-a.json`
      );
    });

    test('returns null when the hierarchy file has not been generated (404)', async () => {
      fetchMock.mockResolvedValue(jsonResponse(null, false, 404));
      const Utils = await loadUtils();
      await expect(Utils.fetchTreeData('repo-x')).resolves.toBeNull();
      expect(console.error).not.toHaveBeenCalled();
    });

    test.each([[null], [{ repository: 'repo-a' }], [{ hierarchy: 'oops' }]])(
      'returns null when the file has no tree (%j)',
      async (body) => {
        fetchMock.mockResolvedValue(jsonResponse(body));
        const Utils = await loadUtils();
        await expect(Utils.fetchTreeData('repo-a')).resolves.toBeNull();
      }
    );

    test('propagates real failures', async () => {
      fetchMock.mockRejectedValue(new Error('dns'));
      const Utils = await loadUtils();
      await expect(Utils.fetchTreeData('repo-y')).rejects.toThrow('dns');
      expect(console.error).toHaveBeenCalledWith(
        'Error fetching tree data for repo-y:',
        expect.objectContaining({ message: 'dns' })
      );

      fetchMock.mockResolvedValue(jsonResponse(null, false, 500));
      await expect(Utils.fetchTreeData('repo-y')).rejects.toThrow('(status: 500)');
    });
  });

  describe('getLanguageColor', () => {
    test.each([
      ['JavaScript', '#f1e05a'],
      ['TypeScript', '#2b7489'],
      ['Python', '#3572A5'],
      ['C++', '#f34b7d'],
      ['C#', '#178600'],
      ['Svelte', '#ff3e00'],
    ])('returns known color for %s', async (lang, color) => {
      const Utils = await loadUtils();
      expect(Utils.getLanguageColor(lang)).toBe(color);
    });

    test('falls back to grey for unknown languages', async () => {
      const Utils = await loadUtils();
      expect(Utils.getLanguageColor('COBOL')).toBe('#8e8e8e');
      expect(Utils.getLanguageColor('')).toBe('#8e8e8e');
    });
  });

  describe('formatBytes', () => {
    test.each([
      [0, '0 Bytes'],
      [1, '1 Bytes'],
      [512, '512 Bytes'],
      [1024, '1 KB'],
      [1536, '1.5 KB'],
      [1234567, '1.18 MB'],
      [1024 ** 3, '1 GB'],
      [5.25 * 1024 ** 3, '5.25 GB'],
    ])('formats %d as %s', async (bytes, expected) => {
      const Utils = await loadUtils();
      expect(Utils.formatBytes(bytes)).toBe(expected);
    });
  });
});
