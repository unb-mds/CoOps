import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  DataNotFoundError,
  DataUnconfiguredError,
  fetchAvailableRepoNames,
  fetchData,
  filterMetadata,
  getDataBasePath,
  isDataNotFoundError,
  isDataUnconfiguredError,
} from './dataSource';

// Remote mode is the default in the test environment (VITE_USE_LOCAL_DATA is
// unset), so give every test a configured organization unless it opts out with
// vi.unstubAllEnvs() to exercise the fail-closed path.
beforeEach(() => {
  vi.stubEnv('VITE_GITHUB_ORG', 'test-org');
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('dataSource service', () => {
  describe('filterMetadata', () => {
    test('remove entradas com _metadata', () => {
      const data: Record<string, unknown>[] = [{ id: 1 }, { _metadata: { generated: 'now' } }, { id: 2 }];
      expect(filterMetadata(data)).toEqual([{ id: 1 }, { id: 2 }]);
    });

    test('remove entradas null ou undefined sem lançar erro', () => {
      const data: (Record<string, unknown> | null | undefined)[] = [{ id: 1 }, null, undefined, { id: 2 }];
      expect(filterMetadata(data)).toEqual([{ id: 1 }, { id: 2 }]);
    });
  });

  describe('fetchAvailableRepoNames', () => {
    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    const respond = (body: unknown) =>
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body }));

    test('lê silver/available_repos.json pelo dataSource', async () => {
      respond(['repo-a', 'repo-b']);
      await expect(fetchAvailableRepoNames()).resolves.toEqual(['repo-a', 'repo-b']);
      expect(fetch).toHaveBeenCalledWith(`${getDataBasePath()}/silver/available_repos.json`);
    });

    test('tolera uma entrada de _metadata e ignora valores que não são strings', async () => {
      respond([{ _metadata: { extracted_at: 'now' } }, 'repo-a', null, 3, { name: 'x' }, 'repo-b']);
      await expect(fetchAvailableRepoNames()).resolves.toEqual(['repo-a', 'repo-b']);
    });

    test('retorna lista vazia quando o conteúdo não é um array', async () => {
      respond({ repos: ['repo-a'] });
      await expect(fetchAvailableRepoNames()).resolves.toEqual([]);
    });

    test('propaga DataNotFoundError quando o arquivo não existe', async () => {
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: '' }));
      await expect(fetchAvailableRepoNames()).rejects.toBeInstanceOf(DataNotFoundError);
    });
  });

  describe('fetchData', () => {
    beforeEach(() => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      vi.spyOn(console, 'warn').mockImplementation(() => {});
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    test('busca o caminho relativo à base de dados e retorna o JSON', async () => {
      const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [1, 2] });
      vi.stubGlobal('fetch', fetchMock);

      await expect(fetchData('silver/file.json')).resolves.toEqual([1, 2]);
      expect(fetchMock).toHaveBeenCalledWith(`${getDataBasePath()}/silver/file.json`);
    });

    test('lança DataNotFoundError com o caminho e a URL quando o arquivo não existe (404)', async () => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: '' }));

      const error = await fetchData('silver/missing.json').catch((e: unknown) => e);
      expect(error).toBeInstanceOf(DataNotFoundError);
      expect(isDataNotFoundError(error)).toBe(true);
      const notFound = error as DataNotFoundError;
      expect(notFound.name).toBe('DataNotFoundError');
      expect(notFound.path).toBe('silver/missing.json');
      expect(notFound.url).toBe(`${getDataBasePath()}/silver/missing.json`);
      expect(notFound.message).toMatch(/silver\/missing\.json \(status: 404\)$/);
      // um arquivo ainda não gerado não é tratado como erro
      expect(console.error).not.toHaveBeenCalled();
      expect(console.warn).toHaveBeenCalled();
    });

    test('inclui o status HTTP na mensagem quando statusText está vazio', async () => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: '' }));

      const error = await fetchData('silver/unavailable.json').catch((e: unknown) => e);
      expect(isDataNotFoundError(error)).toBe(false);
      expect((error as Error).message).toMatch(
        /Failed to fetch .*silver\/unavailable\.json \(status: 503\)$/
      );
      expect(console.error).toHaveBeenCalled();
    });

    test('JSON inválido continua sendo um erro comum', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          json: async () => {
            throw new SyntaxError('Unexpected token < in JSON');
          },
        })
      );

      const error = await fetchData('silver/file.json').catch((e: unknown) => e);
      expect(error).toBeInstanceOf(SyntaxError);
      expect(isDataNotFoundError(error)).toBe(false);
    });

    test('isDataNotFoundError rejeita valores que não são DataNotFoundError', () => {
      expect(isDataNotFoundError(new Error('status: 404'))).toBe(false);
      expect(isDataNotFoundError(null)).toBe(false);
      expect(isDataNotFoundError('silver/x.json')).toBe(false);
    });

    test('inclui status e statusText quando disponível', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: 'Internal Server Error' })
      );

      await expect(fetchData('silver/broken.json')).rejects.toThrow(
        '(status: 500 Internal Server Error)'
      );
    });

    test('propaga erros de rede', async () => {
      vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network down')));

      await expect(fetchData('silver/file.json')).rejects.toThrow('Network down');
      expect(console.error).toHaveBeenCalled();
    });
  });

  describe('configuration guard (VITE_GITHUB_ORG)', () => {
    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    test('fails closed without VITE_GITHUB_ORG: no fetch, DataUnconfiguredError', async () => {
      vi.unstubAllEnvs();
      const fetchMock = vi.fn();
      vi.stubGlobal('fetch', fetchMock);

      const error = await fetchData('silver/file.json').catch((e: unknown) => e);

      expect(error).toBeInstanceOf(DataUnconfiguredError);
      expect(isDataUnconfiguredError(error)).toBe(true);
      expect((error as DataUnconfiguredError).name).toBe('DataUnconfiguredError');
      expect(fetchMock).not.toHaveBeenCalled();
    });

    test('getDataBasePath throws DataUnconfiguredError without VITE_GITHUB_ORG', () => {
      vi.unstubAllEnvs();

      expect(() => getDataBasePath()).toThrow(DataUnconfiguredError);
    });

    test('isDataUnconfiguredError rejeita valores que não são DataUnconfiguredError', () => {
      expect(isDataUnconfiguredError(new DataNotFoundError('silver/x.json', 'https://x/data'))).toBe(false);
      expect(isDataUnconfiguredError(new Error('VITE_GITHUB_ORG is not set'))).toBe(false);
      expect(isDataUnconfiguredError(null)).toBe(false);
    });

    test('targets the configured organization URL', async () => {
      vi.stubEnv('VITE_GITHUB_ORG', 'acme-org');
      const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] });
      vi.stubGlobal('fetch', fetchMock);

      await fetchData('silver/file.json');

      expect(fetchMock).toHaveBeenCalledWith(
        'https://raw.githubusercontent.com/acme-org/CoOps/main/data/silver/file.json'
      );
    });

    test('getDataBasePath returns the configured organization URL', () => {
      vi.stubEnv('VITE_GITHUB_ORG', 'acme-org');

      expect(getDataBasePath()).toBe('https://raw.githubusercontent.com/acme-org/CoOps/main/data');
    });
  });
});
