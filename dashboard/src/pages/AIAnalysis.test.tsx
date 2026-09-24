import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AIAnalysis from './AIAnalysis';
import { SidebarProvider } from '../contexts/SidebarContext';
import { DataNotFoundError, fetchData } from '../services/dataSource';

vi.mock('../services/dataSource', async () => {
  const actual = await vi.importActual<typeof import('../services/dataSource')>(
    '../services/dataSource'
  );
  return { ...actual, fetchData: vi.fn() };
});

const mockedFetchData = vi.mocked(fetchData);

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/ai']}>
      <SidebarProvider>
        <AIAnalysis />
      </SidebarProvider>
    </MemoryRouter>
  );

describe('AIAnalysis page', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    mockedFetchData.mockReset();
  });

  test('renders the header, the member selector and highlights the sidebar entry', async () => {
    mockedFetchData.mockResolvedValue({
      _metadata: { total_members: 1 },
      members: {
        'member-a': {
          id: 'member-a',
          name: 'member-a',
          repos: ['repo-x'],
          commits_analysis: 'c',
          prs_analysis: 'p',
          issues_analysis: 'i',
        },
      },
    });

    renderPage();

    expect(screen.getByRole('heading', { level: 1, name: 'AI Member Analysis' })).toBeInTheDocument();
    const selector = screen.getByText('Select members').closest('button');
    expect(selector).toBeInTheDocument();
    // The loaded member shows up in the selector: the data has arrived
    fireEvent.click(selector!);
    expect(await screen.findByText('member-a')).toBeInTheDocument();
    expect(screen.getByText('AI Analysis').closest('button')).toHaveClass('text-blue-300');
    expect(mockedFetchData).toHaveBeenCalledWith('silver/ai/members_ai.json');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  test('explains that AI analysis needs GEMINI_API_KEY when the file is missing', async () => {
    mockedFetchData.mockRejectedValue(
      new DataNotFoundError('silver/ai/members_ai.json', 'https://x/data/silver/ai/members_ai.json')
    );

    renderPage();

    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent("This data hasn't been generated yet");
    expect(status).toHaveTextContent('data/silver/ai/members_ai.json');
    expect(status).toHaveTextContent(/requires a GEMINI_API_KEY repository secret/);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
