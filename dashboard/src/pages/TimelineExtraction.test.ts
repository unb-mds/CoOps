import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { TimelineExtraction, TimelineData } from './TimelineExtraction';
import { fetchData } from '../services/dataSource';

// Mock the dataSource service
vi.mock('../services/dataSource', () => ({
  fetchData: vi.fn(),
  filterMetadata: vi.fn((data) => data.filter((item: any) => !item._metadata)),
}));

describe('TimelineExtraction Class', () => {
  const mockRawData = [
    {
      _metadata: { version: '1.0' }, // Should be filtered out
    },
    {
      date: '2024-01-01',
      authors: [
        {
          name: 'User One',
          repositories: ['repo1', '2025-2-Squad-01'],
          commits: 5,
          issues_created: 2,
          issues_closed: 1,
          prs_created: 3,
          prs_closed: 2,
          comments: 10,
        },
        {
          name: 'User Two',
          repositories: ['repo2'],
          commits: 3,
          issues_created: 1,
          issues_closed: 0,
          prs_created: 1,
          prs_closed: 1,
          comments: 5,
        },
      ],
    },
    {
      date: '2024-01-02',
      authors: [
        {
          name: 'User Three',
          repositories: ['2025-2-Squad-01'],
          commits: 8,
          issues_created: 0,
          issues_closed: 0,
          prs_created: 2,
          prs_closed: 0,
          comments: 3,
        },
      ],
    },
    {
      date: '2024-01-03',
      authors: [], // Empty day - should be kept
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ========== EXTRACTTIMELINEDATA - SUCCESS CASES ==========
  describe('extractTimelineData - Success Cases', () => {
    test('fetches data for last_7_days', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_7_days');

      expect(fetchData).toHaveBeenCalledWith('gold/timeline_last_7_days.json');
      expect(result).toBeDefined();
      expect(Array.isArray(result)).toBe(true);
    });

    test('fetches data for last_12_months', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_12_months');

      expect(fetchData).toHaveBeenCalledWith('gold/timeline_last_12_months.json');
      expect(result).toBeDefined();
    });

    test('fetches data with repo_filter', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_7_days', '2025-2-Squad-01');

      expect(result).toBeDefined();
      expect(fetchData).toHaveBeenCalled();
    });

    test('processes data correctly after fetch', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_7_days');

      // Verify metadata was removed
      expect(result.length).toBe(3); // 3 days without metadata
    });

    test('returns TimelineData array with correct structure', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_7_days');

      expect(result[0]).toHaveProperty('date');
      expect(result[0]).toHaveProperty('users');
      expect(Array.isArray(result[0].users)).toBe(true);
    });
  });

  // ========== EXTRACTTIMELINEDATA - PATH SELECTION ==========
  describe('extractTimelineData - Path Selection', () => {
    test('selects correct path for last_7_days', async () => {
      (fetchData as any).mockResolvedValue([]);

      await TimelineExtraction.extractTimelineData('last_7_days');

      expect(fetchData).toHaveBeenCalledWith('gold/timeline_last_7_days.json');
    });

    test('selects correct path for last_12_months', async () => {
      (fetchData as any).mockResolvedValue([]);

      await TimelineExtraction.extractTimelineData('last_12_months');

      expect(fetchData).toHaveBeenCalledWith('gold/timeline_last_12_months.json');
    });

    test('path includes gold directory', async () => {
      (fetchData as any).mockResolvedValue([]);

      await TimelineExtraction.extractTimelineData('last_7_days');

      expect(fetchData).toHaveBeenCalledWith(
        expect.stringContaining('gold/')
      );
    });
  });

  // ========== EXTRACTTIMELINEDATA - ERROR HANDLING ==========
  describe('extractTimelineData - Error Handling', () => {
    test('throws error for invalid time_filter', async () => {
      await expect(
        TimelineExtraction.extractTimelineData('invalid_filter')
      ).rejects.toThrow('Invalid time filter: invalid_filter');
    });

    test('error message includes valid filters', async () => {
      await expect(
        TimelineExtraction.extractTimelineData('wrong')
      ).rejects.toThrow("Use 'last_7_days' or 'last_12_months'");
    });

    test('throws error when fetchData fails with not found', async () => {
      (fetchData as any).mockRejectedValue(
        new Error('Failed to fetch https://raw.githubusercontent.com/example-org/CoOps/main/data/gold/timeline_last_7_days.json: Not Found')
      );

      await expect(
        TimelineExtraction.extractTimelineData('last_7_days')
      ).rejects.toThrow('Failed to fetch');
    });

    test('throws error when fetchData fails with server error', async () => {
      (fetchData as any).mockRejectedValue(
        new Error('Failed to fetch https://raw.githubusercontent.com/example-org/CoOps/main/data/gold/timeline_last_7_days.json: Internal Server Error')
      );

      await expect(
        TimelineExtraction.extractTimelineData('last_7_days')
      ).rejects.toThrow('Internal Server Error');
    });

    test('throws error when fetchData throws a JSON parse error', async () => {
      (fetchData as any).mockRejectedValue(new Error('JSON parse error'));

      await expect(
        TimelineExtraction.extractTimelineData('last_7_days')
      ).rejects.toThrow('JSON parse error');
    });

    test('throws error when fetchData rejects with network error', async () => {
      (fetchData as any).mockRejectedValue(new Error('Network error'));

      await expect(
        TimelineExtraction.extractTimelineData('last_7_days')
      ).rejects.toThrow('Network error');
    });
  });

  // ========== PROCESSTIMELINEDATA - DATA MAPPING ==========
  describe('processTimelineData - Data Mapping', () => {
    test('correctly maps date', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].date).toBe('2024-01-01');
      expect(result[1].date).toBe('2024-01-02');
    });

    test('correctly maps users', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users.length).toBe(2);
      expect(result[1].users.length).toBe(1);
    });

    test('correctly maps user names', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users[0].name).toBe('User One');
      expect(result[0].users[1].name).toBe('User Two');
    });

    test('correctly maps repositories', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users[0].repositories).toEqual(['repo1', '2025-2-Squad-01']);
      expect(result[0].users[1].repositories).toEqual(['repo2']);
    });

    test('correctly maps activities.commits', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users[0].activities.commits).toBe(5);
      expect(result[0].users[1].activities.commits).toBe(3);
    });

    test('correctly maps activities.issues_created', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users[0].activities.issues_created).toBe(2);
      expect(result[0].users[1].activities.issues_created).toBe(1);
    });

    test('correctly maps activities.issues_closed', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users[0].activities.issues_closed).toBe(1);
      expect(result[0].users[1].activities.issues_closed).toBe(0);
    });

    test('correctly maps activities.prs_created', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users[0].activities.prs_created).toBe(3);
      expect(result[0].users[1].activities.prs_created).toBe(1);
    });

    test('correctly maps activities.prs_closed', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users[0].activities.prs_closed).toBe(2);
      expect(result[0].users[1].activities.prs_closed).toBe(1);
    });

    test('correctly maps activities.comments', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users[0].activities.comments).toBe(10);
      expect(result[0].users[1].activities.comments).toBe(5);
    });
  });

  // ========== PROCESSTIMELINEDATA - METADATA FILTERING ==========
  describe('processTimelineData - Metadata Filtering', () => {
    test('removes entry with _metadata', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result.length).toBe(3); // 4 items - 1 metadata
    });

    test('does not include item with _metadata in result', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      const hasMetadata = result.some((item: any) => item._metadata !== undefined);
      expect(hasMetadata).toBe(false);
    });

    test('filters metadata even when it is in the middle of the array', () => {
      const dataWithMetadataInMiddle = [
        { date: '2024-01-01', authors: [] },
        { _metadata: { version: '1.0' } },
        { date: '2024-01-02', authors: [] },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithMetadataInMiddle);

      expect(result.length).toBe(2);
    });

    test('filters multiple metadata entries', () => {
      const dataWithMultipleMetadata = [
        { _metadata: { version: '1.0' } },
        { date: '2024-01-01', authors: [] },
        { _metadata: { other: 'data' } },
        { date: '2024-01-02', authors: [] },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithMultipleMetadata);

      expect(result.length).toBe(2);
    });

    test('keeps valid data after filtering metadata', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].date).toBe('2024-01-01');
      expect(result[1].date).toBe('2024-01-02');
    });
  });

  // ========== PROCESSTIMELINEDATA - REPOSITORY FILTER ==========
  describe('processTimelineData - Repository Filter', () => {
    test('filters users by specific repository', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData, '2025-2-Squad-01');

      // Only User One and User Three have 2025-2-Squad-01
      expect(result[0].users.length).toBe(1); // User One
      expect(result[0].users[0].name).toBe('User One');
    });

    test('filter is case-insensitive', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData, 'SQUAD-01');

      expect(result[0].users.length).toBe(1);
      expect(result[0].users[0].name).toBe('User One');
    });

    test('filter "all" returns all users', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData, 'all');

      expect(result[0].users.length).toBe(2);
    });

    test('no filter returns all users', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users.length).toBe(2);
    });

    test('undefined filter returns all users', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData, undefined);

      expect(result[0].users.length).toBe(2);
    });

    test('substring filter works', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData, 'Squad');

      expect(result[0].users.length).toBe(1);
      expect(result[0].users[0].name).toBe('User One');
    });

    test('filter for nonexistent repo returns empty users array', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData, 'nonexistent-repo');

      expect(result[0].users.length).toBe(0);
    });

    test('keeps day structure even without users after filtering', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData, 'nonexistent-repo');

      expect(result.length).toBe(3); // Keeps all 3 days
      expect(result[0].date).toBe('2024-01-01');
    });

    test('filter searches across all user repositories', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData, 'repo1');

      expect(result[0].users.length).toBe(1);
      expect(result[0].users[0].name).toBe('User One');
    });
  });

  // ========== PROCESSTIMELINEDATA - DEFAULT VALUES ==========
  describe('processTimelineData - Default Values', () => {
    test('uses empty array for repositories when absent', () => {
      const dataWithoutRepos = [
        {
          date: '2024-01-01',
          authors: [
            {
              name: 'User',
              commits: 5,
            },
          ],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithoutRepos);

      expect(result[0].users[0].repositories).toEqual([]);
    });

    test('uses 0 for commits when absent', () => {
      const dataWithoutCommits = [
        {
          date: '2024-01-01',
          authors: [{ name: 'User' }],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithoutCommits);

      expect(result[0].users[0].activities.commits).toBe(0);
    });

    test('uses 0 for issues_created when absent', () => {
      const dataWithoutIssues = [
        {
          date: '2024-01-01',
          authors: [{ name: 'User' }],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithoutIssues);

      expect(result[0].users[0].activities.issues_created).toBe(0);
    });

    test('uses 0 for issues_closed when absent', () => {
      const dataWithoutIssues = [
        {
          date: '2024-01-01',
          authors: [{ name: 'User' }],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithoutIssues);

      expect(result[0].users[0].activities.issues_closed).toBe(0);
    });

    test('uses 0 for prs_created when absent', () => {
      const dataWithoutPRs = [
        {
          date: '2024-01-01',
          authors: [{ name: 'User' }],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithoutPRs);

      expect(result[0].users[0].activities.prs_created).toBe(0);
    });

    test('uses 0 for prs_closed when absent', () => {
      const dataWithoutPRs = [
        {
          date: '2024-01-01',
          authors: [{ name: 'User' }],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithoutPRs);

      expect(result[0].users[0].activities.prs_closed).toBe(0);
    });

    test('uses 0 for comments when absent', () => {
      const dataWithoutComments = [
        {
          date: '2024-01-01',
          authors: [{ name: 'User' }],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithoutComments);

      expect(result[0].users[0].activities.comments).toBe(0);
    });

    test('all defaults applied simultaneously', () => {
      const minimalData = [
        {
          date: '2024-01-01',
          authors: [{ name: 'User' }],
        },
      ];

      const result = TimelineExtraction.processTimelineData(minimalData);

      expect(result[0].users[0]).toEqual({
        name: 'User',
        repositories: [],
        activities: {
          commits: 0,
          issues_created: 0,
          issues_closed: 0,
          prs_created: 0,
          prs_closed: 0,
          comments: 0,
        },
      });
    });
  });

  // ========== PROCESSTIMELINEDATA - EMPTY DAYS ==========
  describe('processTimelineData - Empty Days', () => {
    test('keeps days without users', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[2].date).toBe('2024-01-03');
      expect(result[2].users.length).toBe(0);
    });

    test('does not remove empty days from array', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result.length).toBe(3); // Includes empty day
    });

    test('keeps correct structure for empty day', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[2]).toHaveProperty('date');
      expect(result[2]).toHaveProperty('users');
      expect(Array.isArray(result[2].users)).toBe(true);
    });

    test('empty day does not affect processing of other days', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      expect(result[0].users.length).toBe(2);
      expect(result[1].users.length).toBe(1);
      expect(result[2].users.length).toBe(0);
    });

    test('multiple empty days are kept', () => {
      const dataWithMultipleEmptyDays = [
        { date: '2024-01-01', authors: [{ name: 'User' }] },
        { date: '2024-01-02', authors: [] },
        { date: '2024-01-03', authors: [] },
        { date: '2024-01-04', authors: [{ name: 'User' }] },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithMultipleEmptyDays);

      expect(result.length).toBe(4);
      expect(result[1].users.length).toBe(0);
      expect(result[2].users.length).toBe(0);
    });
  });

  // ========== EDGE CASES ==========
  describe('Edge Cases', () => {
    test('processes empty array', () => {
      const result = TimelineExtraction.processTimelineData([]);

      expect(result).toEqual([]);
    });

    test('processes data with only metadata', () => {
      const onlyMetadata = [{ _metadata: { version: '1.0' } }];

      const result = TimelineExtraction.processTimelineData(onlyMetadata);

      expect(result).toEqual([]);
    });

    test('processes day with author without name', () => {
      const dataWithoutName = [
        {
          date: '2024-01-01',
          authors: [{ commits: 5 }],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithoutName);

      expect(result[0].users[0].name).toBeUndefined();
    });

    test('processes data with special characters in name', () => {
      const dataWithSpecialChars = [
        {
          date: '2024-01-01',
          authors: [
            {
              name: 'User@#$%',
              repositories: ['repo-with-dash_underscore'],
              commits: 1,
            },
          ],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithSpecialChars);

      expect(result[0].users[0].name).toBe('User@#$%');
    });

    test('processes data with negative values', () => {
      const dataWithNegatives = [
        {
          date: '2024-01-01',
          authors: [
            {
              name: 'User',
              commits: -5,
              issues_created: -2,
            },
          ],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithNegatives);

      expect(result[0].users[0].activities.commits).toBe(-5);
      expect(result[0].users[0].activities.issues_created).toBe(-2);
    });

    test('processes data with very large values', () => {
      const dataWithLargeNumbers = [
        {
          date: '2024-01-01',
          authors: [
            {
              name: 'User',
              commits: 999999,
              comments: 888888,
            },
          ],
        },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithLargeNumbers);

      expect(result[0].users[0].activities.commits).toBe(999999);
      expect(result[0].users[0].activities.comments).toBe(888888);
    });

    test('processes dates in different formats', () => {
      const dataWithDifferentDateFormat = [
        { date: '2024-01-01T10:00:00Z', authors: [] },
        { date: '2024/01/02', authors: [] },
        { date: '01-03-2024', authors: [] },
      ];

      const result = TimelineExtraction.processTimelineData(dataWithDifferentDateFormat);

      expect(result[0].date).toBe('2024-01-01T10:00:00Z');
      expect(result[1].date).toBe('2024/01/02');
      expect(result[2].date).toBe('01-03-2024');
    });
  });

  // ========== INTEGRATION ==========
  describe('Integration Tests', () => {
    test('extractTimelineData processes data correctly end-to-end', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_7_days');

      expect(result.length).toBe(3);
      expect(result[0].users.length).toBe(2);
      expect(result[0].users[0].name).toBe('User One');
    });

    test('extractTimelineData with filter applies filter correctly', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_7_days', '2025-2-Squad-01');

      expect(result[0].users.length).toBe(1);
      expect(result[0].users[0].name).toBe('User One');
    });

    test('full flow last_7_days without filter', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_7_days');

      expect(fetchData).toHaveBeenCalledTimes(1);
      expect(result).toHaveLength(3);
      expect(result[0]).toHaveProperty('date');
      expect(result[0]).toHaveProperty('users');
    });

    test('full flow last_12_months with filter', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result = await TimelineExtraction.extractTimelineData('last_12_months', 'repo2');

      expect(fetchData).toHaveBeenCalledWith('gold/timeline_last_12_months.json');
      expect(result[0].users[0].name).toBe('User Two');
    });
  });

  // ========== TYPE SAFETY ==========
  describe('Type Safety', () => {
    test('returns TimelineData[] type', async () => {
      (fetchData as any).mockResolvedValue(mockRawData);

      const result: TimelineData[] = await TimelineExtraction.extractTimelineData('last_7_days');

      expect(Array.isArray(result)).toBe(true);
    });

    test('each item has TimelineData structure', () => {
      const result = TimelineExtraction.processTimelineData(mockRawData);

      result.forEach((item) => {
        expect(typeof item.date).toBe('string');
        expect(Array.isArray(item.users)).toBe(true);

        item.users.forEach((user) => {
          expect(user).toHaveProperty('activities');
          expect(typeof user.activities.commits).toBe('number');
          expect(typeof user.activities.issues_created).toBe('number');
          expect(typeof user.activities.issues_closed).toBe('number');
          expect(typeof user.activities.prs_created).toBe('number');
          expect(typeof user.activities.prs_closed).toBe('number');
          expect(typeof user.activities.comments).toBe('number');
        });
      });
    });
  });

  // ========== PERFORMANCE ==========
  describe('Performance', () => {
    test('processes large volume of data efficiently', () => {
      const largeMockData = Array.from({ length: 1000 }, (_, i) => ({
        date: `2024-01-${(i % 31) + 1}`,
        authors: Array.from({ length: 10 }, (_, j) => ({
          name: `User ${j}`,
          repositories: [`repo${j}`],
          commits: Math.floor(Math.random() * 100),
          issues_created: Math.floor(Math.random() * 10),
          issues_closed: Math.floor(Math.random() * 10),
          prs_created: Math.floor(Math.random() * 5),
          prs_closed: Math.floor(Math.random() * 5),
          comments: Math.floor(Math.random() * 50),
        })),
      }));

      const startTime = performance.now();
      const result = TimelineExtraction.processTimelineData(largeMockData);
      const endTime = performance.now();

      expect(result.length).toBe(1000);
      expect(endTime - startTime).toBeLessThan(1000); // Less than 1 second
    });

    test('repository filter does not degrade performance', () => {
      const largeMockData = Array.from({ length: 500 }, (_, i) => ({
        date: `2024-01-${(i % 31) + 1}`,
        authors: Array.from({ length: 20 }, (_, j) => ({
          name: `User ${j}`,
          repositories: [`repo${j}`, '2025-2-Squad-01'],
          commits: 1,
        })),
      }));

      const startTime = performance.now();
      const result = TimelineExtraction.processTimelineData(largeMockData, '2025-2-Squad-01');
      const endTime = performance.now();

      expect(result.length).toBe(500);
      expect(endTime - startTime).toBeLessThan(1000);
    });
  });
});
