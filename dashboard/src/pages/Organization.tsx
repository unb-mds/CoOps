import { useEffect, useState } from 'react';
import DashboardLayout from '../components/DashboardLayout';
import Loading from '../components/Loading';
import DataNotGenerated, { DataLoadError, DataNotConfigured } from '../components/DataNotGenerated';
import { PieChart, ScatterPlot, Histogram } from '../components/charts';
import { fetchData, filterMetadata, isDataNotFoundError, isDataUnconfiguredError } from '../services/dataSource';

interface MemberAnalytics {
  login: string;
  maturity_score: number;
  status: string;
  public_repos: number;
  followers: number;
}

interface ContributionMetrics {
  user: string;
  total_contributions: number;
  commits: number;
  prs_authored: number;
  issues_created: number;
  has_contributed: boolean;
}

/**
 * Organization Page
 *
 * High-level overview of organization-wide metrics and team structure.
 * Shows member distribution, maturity analysis, and follower statistics.
 */
export default function Organization() {
  const [membersData, setMembersData] = useState<MemberAnalytics[]>([]);
  const [contributionsData, setContributionsData] = useState<ContributionMetrics[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [missingDataPath, setMissingDataPath] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);

  useEffect(() => {
    async function loadData() {
      try {
        const [members, contributions] = await Promise.all([
          fetchData('silver/members_analytics.json'),
          fetchData('silver/contribution_metrics.json'),
        ]);

        setMembersData(filterMetadata(members));
        setContributionsData(filterMetadata(contributions));
        setLoading(false);
      } catch (error) {
        if (isDataNotFoundError(error)) {
          setMissingDataPath(error.path);
        } else if (isDataUnconfiguredError(error)) {
          setNotConfigured(true);
        } else {
          console.error('Failed to load organization data:', error);
          setError(error instanceof Error ? error.message : String(error));
        }
        setLoading(false);
      }
    }

    loadData();
  }, []);

  // Member status distribution
  const memberStatusPieData = membersData.reduce((acc, m) => {
    const status = m.status === 'new' ? 'New Members' : 'Established Members';
    const existing = acc.find((d) => d.label === status);
    if (existing) {
      existing.value++;
    } else {
      acc.push({ label: status, value: 1 });
    }
    return acc;
  }, [] as { label: string; value: number }[]);

  // Maturity vs Contributions scatter
  const maturityScatterData = membersData.map((m) => {
    const contrib = contributionsData.find((c) => c.user === m.login);
    return {
      x: m.maturity_score,
      y: contrib?.total_contributions || 0,
      label: m.login,
      category: m.status,
    };
  });

  // Followers histogram
  const followersHistogramData = membersData.map((m) => m.followers);

  if (loading) {
    return (
      <DashboardLayout currentPage="organization" currentSubPage="" onRepo={false}>
        <Loading message="Loading organization metrics..." size="lg" />
      </DashboardLayout>
    );
  }

  if (notConfigured || missingDataPath || error) {
    return (
      <DashboardLayout currentPage="organization" currentSubPage="" onRepo={false}>
        {notConfigured ? (
          <DataNotConfigured />
        ) : missingDataPath ? (
          <DataNotGenerated path={missingDataPath} />
        ) : (
          <DataLoadError message={error ?? ''} />
        )}
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout currentPage="organization" currentSubPage="" onRepo={false}>
      <div className="space-y-12">
        {/* Header */}
        <div>
          <h1 className="text-4xl font-bold text-white mb-2">Organization Overview</h1>
          <p className="text-slate-400">
            High-level insights into team structure and member characteristics
          </p>
        </div>

        {/* Member Distribution */}
        <div
          className="border rounded-lg p-6"
          style={{ backgroundColor: '#222222', borderColor: '#333333' }}
        >
          <h3 className="text-xl font-bold text-white mb-4">
            Member Status Distribution
          </h3>
          <p className="text-slate-400 mb-4">
            Distribution of new versus established members in the organization
          </p>

          {/* Classification Criteria */}
          <div className="mb-6 p-4 rounded-lg" style={{ backgroundColor: '#1a1a1a', borderLeft: '4px solid var(--color-blue-trust)' }}>
            <h4 className="text-sm font-bold text-white mb-2">Classification Criteria</h4>
            <div className="text-xs text-slate-300 space-y-1">
              <p><span className="font-semibold text-blue-300">New Members:</span> Account age &lt; 365 days OR (public repos &lt; 10 AND followers &lt; 10)</p>
              <p><span className="font-semibold text-green-300">Established Members:</span> All other members</p>
            </div>
          </div>

          <div className="flex justify-center">
            <PieChart data={memberStatusPieData} width={500} height={500} />
          </div>
        </div>

        {/* Maturity vs Contributions */}
        <div
          className="border rounded-lg p-6"
          style={{ backgroundColor: '#222222', borderColor: '#333333' }}
        >
          <h3 className="text-xl font-bold text-white mb-4">
            Member Maturity vs Contributions
          </h3>
          <p className="text-slate-400 mb-4">
            Relationship between member maturity and contribution levels
          </p>

          {/* Maturity Score Formula */}
          <div className="mb-6 p-4 rounded-lg" style={{ backgroundColor: '#1a1a1a', borderLeft: '4px solid var(--color-amber-accent)' }}>
            <h4 className="text-sm font-bold text-white mb-2">Maturity Score Formula</h4>
            <div className="text-xs text-slate-300 font-mono">
              <p className="mb-1">score = 0.5 × log(1 + account_age_days) + 3 × log(1 + public_repos) + 20 × log(1 + followers)</p>
              <p className="text-slate-400 text-[10px] mt-2">
                Higher scores indicate more experienced and active GitHub users
              </p>
            </div>
          </div>

          <ScatterPlot
            data={maturityScatterData}
            width={900}
            height={400}
            xLabel="Maturity Score"
            yLabel="Total Contributions"
          />
        </div>

        {/* Followers Distribution */}
        <div
          className="border rounded-lg p-6"
          style={{ backgroundColor: '#222222', borderColor: '#333333' }}
        >
          <h3 className="text-xl font-bold text-white mb-4">
            Followers Distribution
          </h3>
          <p className="text-slate-400 mb-4">
            Distribution of GitHub followers across organization members
          </p>
          <Histogram
            data={followersHistogramData}
            width={900}
            height={400}
            xLabel="Number of Followers"
            yLabel="Frequency"
          />
        </div>
      </div>
    </DashboardLayout>
  );
}
