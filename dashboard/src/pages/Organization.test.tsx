import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import Organization from './Organization';
import { DataNotFoundError, DataUnconfiguredError, fetchData } from '../services/dataSource';

vi.mock('../services/dataSource', async () => {
  const actual = await vi.importActual<typeof import('../services/dataSource')>(
    '../services/dataSource'
  );
  return { ...actual, fetchData: vi.fn() };
});

vi.mock('../components/DashboardLayout', () => ({
  default: ({ children, currentPage }: { children: ReactNode; currentPage: string }) => (
    <div data-testid="dashboard-layout" data-page={currentPage}>
      {children}
    </div>
  ),
}));

type ChartProps = Record<string, unknown>;

function makeChart(testId: string) {
  return (props: ChartProps) => <div data-testid={testId} data-props={JSON.stringify(props)} />;
}

vi.mock('../components/charts', () => ({
  PieChart: makeChart('pie-chart'),
  ScatterPlot: makeChart('scatter-plot'),
  Histogram: makeChart('histogram'),
}));

const mockedFetchData = vi.mocked(fetchData);

const propsOf = (el: HTMLElement): ChartProps =>
  JSON.parse(el.getAttribute('data-props') ?? '{}') as ChartProps;

const members = [
  { _metadata: { generated_at: '2024' } },
  null,
  { login: 'alice', maturity_score: 42.5, status: 'established', public_repos: 12, followers: 15 },
  { login: 'bob', maturity_score: 3, status: 'new', public_repos: 1, followers: 1 },
  { login: 'eve', maturity_score: 30, status: 'veteran', public_repos: 50, followers: 99 },
];

const contributions = [
  { _metadata: {} },
  { user: 'alice', total_contributions: 12, commits: 10, prs_authored: 1, issues_created: 1, has_contributed: true },
  { user: 'eve', total_contributions: 0, commits: 0, prs_authored: 0, issues_created: 0, has_contributed: false },
];

function setupFetch() {
  const fixtures: Record<string, unknown[]> = {
    'silver/members_analytics.json': members,
    'silver/contribution_metrics.json': contributions,
  };
  mockedFetchData.mockImplementation(async (path: string) => fixtures[path] as never);
}

const renderPage = () =>
  render(
    <BrowserRouter>
      <Organization />
    </BrowserRouter>
  );

describe('Organization page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders loading indicator while fetching', () => {
    mockedFetchData.mockImplementation(() => new Promise<never>(() => {}));
    renderPage();
    expect(screen.getByText('Loading organization metrics...')).toBeInTheDocument();
    expect(screen.queryByText('Organization Overview')).not.toBeInTheDocument();
    expect(screen.getByTestId('dashboard-layout')).toHaveAttribute('data-page', 'organization');
  });

  test('renders header, criteria and formula once loaded', async () => {
    setupFetch();
    renderPage();
    expect(await screen.findByText('Organization Overview')).toBeInTheDocument();
    expect(screen.getByText('Classification Criteria')).toBeInTheDocument();
    expect(screen.getByText('Maturity Score Formula')).toBeInTheDocument();
    expect(mockedFetchData).toHaveBeenCalledWith('silver/members_analytics.json');
    expect(mockedFetchData).toHaveBeenCalledWith('silver/contribution_metrics.json');
  });

  test('aggregates member status into New vs Established (non-"new" counts as established)', async () => {
    setupFetch();
    renderPage();
    await screen.findByText('Organization Overview');
    expect(propsOf(screen.getByTestId('pie-chart')).data).toEqual([
      { label: 'Established Members', value: 2 },
      { label: 'New Members', value: 1 },
    ]);
  });

  test('joins maturity with contributions, defaulting missing contributors to 0', async () => {
    setupFetch();
    renderPage();
    await screen.findByText('Organization Overview');
    const scatter = propsOf(screen.getByTestId('scatter-plot'));
    expect(scatter.data).toEqual([
      { x: 42.5, y: 12, label: 'alice', category: 'established' },
      { x: 3, y: 0, label: 'bob', category: 'new' },
      { x: 30, y: 0, label: 'eve', category: 'veteran' },
    ]);
    expect(scatter.xLabel).toBe('Maturity Score');
  });

  test('passes followers counts to histogram (metadata/null entries removed)', async () => {
    setupFetch();
    renderPage();
    await screen.findByText('Organization Overview');
    expect(propsOf(screen.getByTestId('histogram')).data).toEqual([15, 1, 99]);
  });

  test('handles fetch failure by logging and showing the error instead of charts', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockedFetchData.mockRejectedValue(new Error('network down'));
    renderPage();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Error loading data: network down');
    expect(errorSpy).toHaveBeenCalledWith('Failed to load organization data:', expect.any(Error));
    expect(screen.queryByTestId('pie-chart')).not.toBeInTheDocument();
    expect(screen.queryByTestId('data-not-generated')).not.toBeInTheDocument();
    expect(screen.getByTestId('dashboard-layout')).toHaveAttribute('data-page', 'organization');
  });

  test('shows the not-generated-yet empty state when a data file is missing (404)', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockedFetchData.mockImplementation(async (path: string) => {
      if (path === 'silver/contribution_metrics.json') {
        throw new DataNotFoundError(path, `https://x/data/${path}`);
      }
      return [];
    });
    renderPage();
    const status = await screen.findByTestId('data-not-generated');
    expect(status).toHaveTextContent("This data hasn't been generated yet");
    expect(status).toHaveTextContent('data/silver/contribution_metrics.json');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pie-chart')).not.toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  test('shows the configure-me state when the data source has no VITE_GITHUB_ORG', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockedFetchData.mockRejectedValue(new DataUnconfiguredError());
    renderPage();
    const status = await screen.findByTestId('data-not-configured');
    expect(status).toHaveTextContent('VITE_GITHUB_ORG is not configured');
    expect(screen.queryByTestId('data-not-generated')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pie-chart')).not.toBeInTheDocument();
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
