import type { ReactElement } from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AISummary } from './AI.summary';
import type { MemberAnalysis } from './AI.summary';

// Mock fetchData from dataSource service
vi.mock('../services/dataSource', async () => {
  const actual = await vi.importActual<typeof import('../services/dataSource')>(
    '../services/dataSource'
  );
  return { ...actual, fetchData: vi.fn() };
});

import { DataNotFoundError, fetchData } from '../services/dataSource';

const mockFetchData = fetchData as ReturnType<typeof vi.fn>;

// Mock console.error to keep test output clean
const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

// Helper data
const mockMembersResponse: Record<string, unknown> = {
  _metadata: { generatedAt: '2024-01-01', version: '1.0' },
  members: {
    alice: {
      id: 'alice',
      name: 'Alice',
      repos: ['repo-alpha', 'repo-beta'],
      commits_analysis: 'Alice commits summary',
      prs_analysis: 'Alice PRs summary',
      issues_analysis: 'Alice issues summary',
    },
    bob: {
      id: 'bob',
      name: 'Bob',
      repos: ['repo-beta'],
      commits_analysis: 'Bob commits summary',
      prs_analysis: 'Bob PRs summary',
      issues_analysis: 'Bob issues summary',
    },
    charlie: {
      id: 'charlie',
      name: 'Charlie',
      repos: ['repo-alpha', 'Repo-Beta'],
      commits_analysis: 'Charlie commits summary',
      prs_analysis: 'Charlie PRs summary',
      issues_analysis: 'Charlie issues summary',
    },
  },
};

const renderWithRouter = (
  component: ReactElement,
  initialEntries: string[] = ['/']
) => {
  return render(
    <MemoryRouter initialEntries={initialEntries}>{component}</MemoryRouter>
  );
};

describe('AISummary Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchData.mockResolvedValue(mockMembersResponse);
  });

  // 1. Renders the dropdown button with default and custom titles
  test('renders dropdown button with default title', async () => {
    renderWithRouter(<AISummary />);

    await waitFor(() => {
      expect(screen.getByText('AI Analysis')).toBeInTheDocument();
    });
  });

  test('renders dropdown button with custom title', async () => {
    renderWithRouter(<AISummary title="Custom AI Report" />);

    await waitFor(() => {
      expect(screen.getByText('Custom AI Report')).toBeInTheDocument();
    });
  });

  // 2. Opens/closes dropdown on button click
  test('opens dropdown on button click', async () => {
    renderWithRouter(<AISummary />);

    await waitFor(() => {
      expect(screen.getByText('AI Analysis')).toBeInTheDocument();
    });

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    expect(screen.getByText('Filters')).toBeInTheDocument();
  });

  test('closes dropdown on second button click', async () => {
    renderWithRouter(<AISummary />);

    await waitFor(() => {
      expect(screen.getByText('AI Analysis')).toBeInTheDocument();
    });

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);
    expect(screen.getByText('Filters')).toBeInTheDocument();

    fireEvent.click(button);
    expect(screen.queryByText('Filters')).not.toBeInTheDocument();
  });

  // 3. Loading state shows spinner
  test('shows loading spinner while fetching data', async () => {
    // Make fetchData hang so we can observe loading state
    mockFetchData.mockReturnValue(new Promise(() => {}));

    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    expect(screen.getByText('Loading analyses...')).toBeInTheDocument();
  });

  // 4. Error states
  test('shows FILE_NOT_FOUND error message', async () => {
    mockFetchData.mockRejectedValue(new Error('FILE_NOT_FOUND'));

    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Analysis file not found')).toBeInTheDocument();
      expect(
        screen.getByText('The AI analysis file has not been generated for this project yet.')
      ).toBeInTheDocument();
    });
  });

  test('shows the not-generated-yet empty state when the data file is missing (404)', async () => {
    mockFetchData.mockRejectedValue(
      new DataNotFoundError('silver/ai/members_ai.json', 'https://x/data/silver/ai/members_ai.json')
    );

    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent("This data hasn't been generated yet");
    expect(status).toHaveTextContent('data/silver/ai/members_ai.json');
    expect(status).toHaveTextContent(/requires a GEMINI_API_KEY repository secret/);
    expect(screen.getByText('No AI analysis available yet.')).toBeInTheDocument();
    expect(screen.queryByText('Unable to load analyses')).not.toBeInTheDocument();
    expect(screen.queryByText('Try again')).not.toBeInTheDocument();
  });

  test('shows the not-generated-yet empty state without opening the dropdown', async () => {
    mockFetchData.mockRejectedValue(
      new DataNotFoundError('silver/ai/members_ai.json', 'https://x/data/silver/ai/members_ai.json')
    );

    renderWithRouter(<AISummary />);

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('data/silver/ai/members_ai.json');
    expect(status).toHaveTextContent('GEMINI_API_KEY');
    expect(screen.queryByText('No AI analysis available yet.')).not.toBeInTheDocument();
  });

  test('reads the members_ai.json shape produced by the pipeline', async () => {
    // Trimmed, anonymized sample of the real silver/ai/members_ai.json,
    // plus the `id` the dashboard requires since the identity/display split
    mockFetchData.mockResolvedValue({
      _metadata: {
        total_members: 2,
        model: 'gemini-3.5-flash-lite',
        description: 'Análises de IA sobre atividades dos membros',
        test_mode: false,
      },
      members: {
        'member-a': {
          id: 'member-a',
          name: 'member-a',
          repos: ['2026-2-Squad-X'],
          commits_analysis:
            'NÚMERO: Com um total de 2 commits, o membro apresenta uma participação pontual.\nATOMICIDADE: média de 125.0 linhas por commit.',
          prs_analysis: 'O membro registrou um total de 2 PRs, ambos fechados.',
          issues_analysis: 'O membro não registrou nenhuma issue (Total de issues: 0).',
        },
        'member-b': {
          id: 'member-b',
          name: 'member-b',
          repos: ['2026-2-Squad-X', '2025-1-Squad-Y'],
          commits_analysis: 'Commits de member-b',
          prs_analysis: 'PRs de member-b',
          issues_analysis: 'Issues de member-b',
        },
      },
    });

    renderWithRouter(<AISummary />);
    expect(mockFetchData).toHaveBeenCalledWith('silver/ai/members_ai.json');

    fireEvent.click(screen.getByText('AI Analysis').closest('button')!);
    expect(await screen.findByText('2 members found')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '2025-1-Squad-Y' })).toBeInTheDocument();

    fireEvent.click(screen.getByText('member-a').closest('button')!);
    expect(screen.getByText('Selected Analyses (1)')).toBeInTheDocument();
    expect(screen.getByText(/ATOMICIDADE: média de 125.0 linhas/)).toBeInTheDocument();
    expect(screen.getByText('O membro registrou um total de 2 PRs, ambos fechados.')).toBeInTheDocument();
  });

  test('shows NO_MEMBERS error message', async () => {
    mockFetchData.mockResolvedValue({ members: {} });

    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('No members found in file')).toBeInTheDocument();
      expect(
        screen.getByText('The analysis file exists but contains no member data.')
      ).toBeInTheDocument();
    });
  });

  test('shows generic error message for other errors', async () => {
    mockFetchData.mockRejectedValue(new Error('Network timeout'));

    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Unable to load analyses')).toBeInTheDocument();
      expect(
        screen.getByText('An error occurred while loading the analysis file.')
      ).toBeInTheDocument();
    });
  });

  // 5. Filters _metadata entries (data without name property excluded)
  test('filters out _metadata entries that have no name property', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    // _metadata should not be rendered as a member
    expect(screen.queryByText('_metadata')).not.toBeInTheDocument();
    expect(screen.queryByText('generatedAt')).not.toBeInTheDocument();

    // All valid members should be present
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.getByText('Charlie')).toBeInTheDocument();
  });

  // 6. Name search filter works correctly
  test('filters members by name search', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Type a name...');
    fireEvent.change(searchInput, { target: { value: 'ali' } });

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
      expect(screen.queryByText('Bob')).not.toBeInTheDocument();
      expect(screen.queryByText('Charlie')).not.toBeInTheDocument();
    });
  });

  test('name search is case-insensitive', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Charlie')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Type a name...');
    fireEvent.change(searchInput, { target: { value: 'CHARLIE' } });

    await waitFor(() => {
      expect(screen.getByText('Charlie')).toBeInTheDocument();
      expect(screen.queryByText('Alice')).not.toBeInTheDocument();
    });
  });

  // 7. Repo filter uses exact case-insensitive match (not substring)
  test('repo filter uses exact case-insensitive match, not substring', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    const repoSelect = screen.getByDisplayValue('All repositories');
    fireEvent.change(repoSelect, { target: { value: 'repo-beta' } });

    await waitFor(() => {
      // Alice has 'repo-beta', Bob has 'repo-beta', Charlie has 'Repo-Beta' (case-insensitive match)
      expect(screen.getByText('Alice')).toBeInTheDocument();
      expect(screen.getByText('Bob')).toBeInTheDocument();
      expect(screen.getByText('Charlie')).toBeInTheDocument();
    });
  });

  test('repo filter does not match substrings', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    const repoSelect = screen.getByDisplayValue('All repositories');
    fireEvent.change(repoSelect, { target: { value: 'repo-alpha' } });

    await waitFor(() => {
      // Only Alice and Charlie have 'repo-alpha'
      expect(screen.getByText('Alice')).toBeInTheDocument();
      expect(screen.getByText('Charlie')).toBeInTheDocument();
      expect(screen.queryByText('Bob')).not.toBeInTheDocument();
    });
  });

  // 8. Member selection - selecting/deselecting members, clear all
  test('selects a member on click', async () => {
    const mockOnSelect = vi.fn();
    renderWithRouter(<AISummary onSelectMember={mockOnSelect} />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    const aliceButton = screen.getByText('Alice').closest('button')!;
    fireEvent.click(aliceButton);

    expect(mockOnSelect).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Alice' })
    );

    // Selected member analysis should be displayed
    expect(screen.getByText('Selected Analyses (1)')).toBeInTheDocument();
  });

  test('deselects a member on second click', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    const aliceButton = screen.getByText('Alice').closest('button')!;
    fireEvent.click(aliceButton);

    expect(screen.getByText('Selected Analyses (1)')).toBeInTheDocument();

    // Click again to deselect
    fireEvent.click(aliceButton);

    expect(screen.queryByText('Selected Analyses (1)')).not.toBeInTheDocument();
  });

  test('clear all removes all selected members', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    // Select two members
    const aliceButton = screen.getByText('Alice').closest('button')!;
    const bobButton = screen.getByText('Bob').closest('button')!;
    fireEvent.click(aliceButton);
    fireEvent.click(bobButton);

    expect(screen.getByText('Selected Analyses (2)')).toBeInTheDocument();

    // Click Clear All
    const clearAllButton = screen.getByText('Clear All');
    fireEvent.click(clearAllButton);

    expect(screen.queryByText('Selected Analyses')).not.toBeInTheDocument();
  });

  // 9. Footer counts
  test('shows correct member count in footer', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('3 members found')).toBeInTheDocument();
    });
  });

  test('shows singular "member" when only 1 member found', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Type a name...');
    fireEvent.change(searchInput, { target: { value: 'Alice' } });

    await waitFor(() => {
      expect(screen.getByText('1 member found')).toBeInTheDocument();
    });
  });

  test('shows selected count in footer when members are selected', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    const aliceButton = screen.getByText('Alice').closest('button')!;
    fireEvent.click(aliceButton);

    expect(screen.getByText('1 selected')).toBeInTheDocument();
  });

  test('shows correct selected count for multiple selections', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Alice').closest('button')!);
    fireEvent.click(screen.getByText('Bob').closest('button')!);

    expect(screen.getByText('2 selected')).toBeInTheDocument();
  });

  // 10. Click outside closes dropdown
  test('closes dropdown when clicking outside', async () => {
    renderWithRouter(
      <div>
        <div data-testid="outside">Outside Element</div>
        <AISummary />
      </div>
    );

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Filters')).toBeInTheDocument();
    });

    const outsideElement = screen.getByTestId('outside');
    fireEvent.mouseDown(outsideElement);

    await waitFor(() => {
      expect(screen.queryByText('Filters')).not.toBeInTheDocument();
    });
  });

  test('does not close dropdown when clicking inside', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Filters')).toBeInTheDocument();
    });

    // Click on the filters section itself
    const filtersLabel = screen.getByText('Filters');
    fireEvent.mouseDown(filtersLabel);

    expect(screen.getByText('Filters')).toBeInTheDocument();
  });

  // 11. clearFilters respects defaultAnalysisType
  test('clearFilters does not reset analysis type when defaultAnalysisType is set', async () => {
    renderWithRouter(<AISummary defaultAnalysisType="commits_analysis" />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    // Set a name filter so "Clear filters" button appears
    const searchInput = screen.getByPlaceholderText('Type a name...');
    fireEvent.change(searchInput, { target: { value: 'Alice' } });

    // Click clear filters
    const clearButton = screen.getByText('Clear filters');
    fireEvent.click(clearButton);

    // Name filter should be cleared
    expect(searchInput).toHaveValue('');

    // The analysis type filter dropdown should not be visible when defaultAnalysisType is set
    expect(screen.queryByDisplayValue('All analyses')).not.toBeInTheDocument();
  });

  test('clearFilters resets analysis type to all when no defaultAnalysisType', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    // Change analysis type
    const analysisSelect = screen.getByDisplayValue('All analyses');
    fireEvent.change(analysisSelect, { target: { value: 'commits_analysis' } });

    // Set a name filter so "Clear filters" button appears (it also shows because analysis type changed)
    const clearButton = screen.getByText('Clear filters');
    fireEvent.click(clearButton);

    // Analysis type should be reset to 'all'
    expect(screen.getByDisplayValue('All analyses')).toBeInTheDocument();
  });

  // Additional coverage: hides name filter when showNameFilter is false
  test('hides name filter when showNameFilter is false', async () => {
    renderWithRouter(<AISummary showNameFilter={false} />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    expect(screen.queryByPlaceholderText('Type a name...')).not.toBeInTheDocument();
  });

  // Additional coverage: hides repo filter when showRepoFilter is false
  test('hides repo filter when showRepoFilter is false', async () => {
    renderWithRouter(<AISummary showRepoFilter={false} />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    expect(screen.queryByDisplayValue('All repositories')).not.toBeInTheDocument();
  });

  // Additional coverage: uses jsonUrl prop with fetch when provided
  test('uses jsonUrl prop with fetch when provided', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mockMembersResponse,
    }) as any;

    renderWithRouter(<AISummary jsonUrl="https://example.com/ai.json" />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('https://example.com/ai.json');
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });
  });

  // Additional coverage: handles jsonUrl 404 error
  test('shows FILE_NOT_FOUND when jsonUrl returns 404', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
    }) as any;

    renderWithRouter(<AISummary jsonUrl="https://example.com/missing.json" />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Analysis file not found')).toBeInTheDocument();
    });
  });

  // Additional coverage: remove event listener on unmount
  test('removes mousedown event listener on unmount', () => {
    const removeEventListenerSpy = vi.spyOn(document, 'removeEventListener');

    const { unmount } = renderWithRouter(<AISummary />);
    unmount();

    expect(removeEventListenerSpy).toHaveBeenCalledWith('mousedown', expect.any(Function));
    removeEventListenerSpy.mockRestore();
  });

  // Additional coverage: no members message when all filtered out
  test('shows no members message when search filters out all members', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('Type a name...');
    fireEvent.change(searchInput, { target: { value: 'nonexistent' } });

    await waitFor(() => {
      expect(screen.getByText('No members found')).toBeInTheDocument();
      expect(screen.getByText('Try adjusting the search filters.')).toBeInTheDocument();
    });
  });

  // Additional coverage: no members message when repo filter active
  test('shows repo-specific message when no members match repo filter', async () => {
    // Create data where a specific repo has no members
    const singleMemberData: Record<string, unknown> = {
      members: {
        alice: {
          id: 'alice',
          name: 'Alice',
          repos: ['only-repo'],
          commits_analysis: 'x',
          prs_analysis: 'y',
          issues_analysis: 'z',
        },
      },
    };
    mockFetchData.mockResolvedValue(singleMemberData);

    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });

    // Filter by name that matches no one
    const searchInput = screen.getByPlaceholderText('Type a name...');
    fireEvent.change(searchInput, { target: { value: 'nobody' } });

    // Switch to a repo filter to get the repo-specific message
    const repoSelect = screen.getByDisplayValue('All repositories');
    fireEvent.change(repoSelect, { target: { value: 'only-repo' } });

    await waitFor(() => {
      expect(screen.getByText('No members found')).toBeInTheDocument();
      expect(
        screen.getByText('No analyses available for the selected repository.')
      ).toBeInTheDocument();
    });
  });

  // Additional coverage: member repos shown in list
  test('displays repository names for each member in the list', async () => {
    renderWithRouter(<AISummary />);

    const button = screen.getByText('AI Analysis').closest('button')!;
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('repo-alpha, repo-beta')).toBeInTheDocument();
    });
  });

  // Additional coverage: fetches from default path when no jsonUrl
  test('fetches from default dataSource path when no jsonUrl is provided', async () => {
    renderWithRouter(<AISummary />);

    await waitFor(() => {
      expect(mockFetchData).toHaveBeenCalledWith('silver/ai/members_ai.json');
    });
  });

  // 12. Identity is `id`, not the display name (issue #151): several people
  // share one name string, and none of them may merge into the other.
  describe('identity keyed on id, not the display name', () => {
    const sameNameDifferentId: Record<string, unknown> = {
      members: {
        'lucas-a': {
          id: 'lucas-a',
          name: 'Lucas Gomes',
          repos: ['repo-one'],
          commits_analysis: 'Commits de lucas-a',
          prs_analysis: 'PRs de lucas-a',
          issues_analysis: 'Issues de lucas-a',
        },
        'lucas-b': {
          id: 'lucas-b',
          name: 'Lucas Gomes',
          repos: ['repo-two'],
          commits_analysis: 'Commits de lucas-b',
          prs_analysis: 'PRs de lucas-b',
          issues_analysis: 'Issues de lucas-b',
        },
      },
    };

    test('renders two members sharing one display name as two rows', async () => {
      mockFetchData.mockResolvedValue(sameNameDifferentId);

      renderWithRouter(<AISummary />);

      fireEvent.click(screen.getByText('AI Analysis').closest('button')!);

      expect(await screen.findAllByText('Lucas Gomes')).toHaveLength(2);
      expect(screen.getByText('2 members found')).toBeInTheDocument();
    });

    test('selecting one same-named member selects only that member', async () => {
      mockFetchData.mockResolvedValue(sameNameDifferentId);

      renderWithRouter(<AISummary />);

      fireEvent.click(screen.getByText('AI Analysis').closest('button')!);
      const rows = (await screen.findAllByText('Lucas Gomes')).map(
        heading => heading.closest('button')!
      );
      expect(rows).toHaveLength(2);

      fireEvent.click(rows[0]);

      expect(screen.getByText('Selected Analyses (1)')).toBeInTheDocument();
      expect(screen.getByText('Commits de lucas-a')).toBeInTheDocument();
      expect(screen.queryByText('Commits de lucas-b')).not.toBeInTheDocument();

      // The second click must ADD the other person, not toggle the shared name off
      fireEvent.click(rows[1]);

      expect(screen.getByText('Selected Analyses (2)')).toBeInTheDocument();
      expect(screen.getByText('Commits de lucas-a')).toBeInTheDocument();
      expect(screen.getByText('Commits de lucas-b')).toBeInTheDocument();
    });

    test('the selection highlight marks only the selected same-named row', async () => {
      mockFetchData.mockResolvedValue(sameNameDifferentId);

      renderWithRouter(<AISummary />);

      fireEvent.click(screen.getByText('AI Analysis').closest('button')!);
      const rows = (await screen.findAllByText('Lucas Gomes')).map(
        heading => heading.closest('button')!
      );

      fireEvent.click(rows[0]);

      expect(rows[0]).toHaveStyle({ backgroundColor: 'rgba(59, 130, 246, 0.2)' });
      expect(rows[1]).not.toHaveStyle({ backgroundColor: 'rgba(59, 130, 246, 0.2)' });
    });

    test('removing one selected card keeps its same-named sibling', async () => {
      mockFetchData.mockResolvedValue(sameNameDifferentId);

      renderWithRouter(<AISummary />);

      fireEvent.click(screen.getByText('AI Analysis').closest('button')!);
      const rows = (await screen.findAllByText('Lucas Gomes')).map(
        heading => heading.closest('button')!
      );
      fireEvent.click(rows[0]);
      fireEvent.click(rows[1]);
      expect(screen.getByText('Selected Analyses (2)')).toBeInTheDocument();

      const removeButtons = screen.getAllByTitle('Remove');
      expect(removeButtons).toHaveLength(2);
      fireEvent.click(removeButtons[0]);

      expect(screen.getByText('Selected Analyses (1)')).toBeInTheDocument();
      expect(screen.getByText('Commits de lucas-b')).toBeInTheDocument();
      expect(screen.queryByText('Commits de lucas-a')).not.toBeInTheDocument();
    });

    test('does not render duplicate React keys when two members share a name', async () => {
      mockFetchData.mockResolvedValue(sameNameDifferentId);

      renderWithRouter(<AISummary />);

      fireEvent.click(screen.getByText('AI Analysis').closest('button')!);

      expect(await screen.findAllByText('Lucas Gomes')).toHaveLength(2);

      const duplicateKeyWarnings = consoleErrorSpy.mock.calls
        .map(call => String(call[0]))
        .filter(message => message.includes('same key'));
      expect(duplicateKeyWarnings).toEqual([]);
    });

    test('the type guard rejects members without an id', async () => {
      mockFetchData.mockResolvedValue({
        members: {
          ok: {
            id: 'ok-1',
            name: 'Has Id',
            repos: [],
            commits_analysis: 'ok commits',
            prs_analysis: 'ok prs',
            issues_analysis: 'ok issues',
          },
          noid: {
            name: 'No Id',
            repos: [],
            commits_analysis: 'x',
            prs_analysis: 'y',
            issues_analysis: 'z',
          },
          emptyid: {
            id: '',
            name: 'Empty Id',
            repos: [],
            commits_analysis: 'x',
            prs_analysis: 'y',
            issues_analysis: 'z',
          },
        },
      });

      renderWithRouter(<AISummary />);

      fireEvent.click(screen.getByText('AI Analysis').closest('button')!);

      expect(await screen.findByText('Has Id')).toBeInTheDocument();
      expect(screen.getByText('1 member found')).toBeInTheDocument();
      expect(screen.queryByText('No Id')).not.toBeInTheDocument();
      expect(screen.queryByText('Empty Id')).not.toBeInTheDocument();
    });

    test('search still filters on the display name, not the id', async () => {
      mockFetchData.mockResolvedValue(sameNameDifferentId);

      renderWithRouter(<AISummary />);

      fireEvent.click(screen.getByText('AI Analysis').closest('button')!);
      await screen.findAllByText('Lucas Gomes');

      const searchInput = screen.getByPlaceholderText('Type a name...');

      // Both same-named members match the shared display name
      fireEvent.change(searchInput, { target: { value: 'lucas' } });
      await waitFor(() => {
        expect(screen.getByText('2 members found')).toBeInTheDocument();
      });

      fireEvent.change(searchInput, { target: { value: 'gomes' } });
      await waitFor(() => {
        expect(screen.getByText('2 members found')).toBeInTheDocument();
      });

      // The id must not be what is searched
      fireEvent.change(searchInput, { target: { value: 'lucas-a' } });
      await waitFor(() => {
        expect(screen.getByText('No members found')).toBeInTheDocument();
      });
    });
  });
});
