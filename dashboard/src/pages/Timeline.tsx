import DashboardLayout from '../components/DashboardLayout';
import { Filter } from '../components/Filter';
import { MemberFilter } from '../components/MemberFilter';
import CalendarHeatmap from '../components/CalendarHeatmap';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { TimelineExtraction, TimelineData } from './TimelineExtraction';
import DataNotGenerated, { DataNotConfigured } from '../components/DataNotGenerated';
import { isDataNotFoundError, isDataUnconfiguredError } from '../services/dataSource';

/**
 * Timeline Component
 *
 * Overview timeline page displaying repository activities.
 */

interface UserActivityData {
  name: string;
  repositories?: string[];
  activities: {
    commits: number;
    issues_created: number;
    issues_closed: number;
    prs_created: number;
    prs_closed: number;
    comments: number;
  };
  dailyValues: number[];
  dailyDetails: Array<{
    commits: number;
    issues_created: number;
    issues_closed: number;
    prs_created: number;
    prs_closed: number;
    comments: number;
  }>;
}

// Function to transform TimelineData[] (grouped by date) into UserActivityData[] (grouped by user)
function transformToUserActivity(timelineData: TimelineData[]): UserActivityData[] {
  const userMap = new Map<string, UserActivityData>();

  // Precompute date→index map for O(1) lookups
  const dateIndexMap = new Map<string, number>();
  timelineData.forEach((entry, idx) => dateIndexMap.set(entry.date, idx));

  // Iterate over each date
  timelineData.forEach((dateEntry) => {
    // Iterate over each user in the date entry
    dateEntry.users.forEach((user) => {
      const userName = user.name || 'Unknown';
      
      if (!userMap.has(userName)) {
        // Create entry for new user
        userMap.set(userName, {
          name: userName,
          repositories: user.repositories ? [...user.repositories] : [],
          activities: {
            commits: 0,
            issues_created: 0,
            issues_closed: 0,
            prs_created: 0,
            prs_closed: 0,
            comments: 0,
          },
          dailyValues: new Array(timelineData.length).fill(0),
          dailyDetails: new Array(timelineData.length).fill(null).map(() => ({
            commits: 0,
            issues_created: 0,
            issues_closed: 0,
            prs_created: 0,
            prs_closed: 0,
            comments: 0,
          })),
        });
      }

      const userData = userMap.get(userName)!;
      const dateIndex = dateIndexMap.get(dateEntry.date) ?? -1;

      // Sum total user activities
      userData.activities.commits += user.activities.commits;
      userData.activities.issues_created += user.activities.issues_created;
      userData.activities.issues_closed += user.activities.issues_closed;
      userData.activities.prs_created += user.activities.prs_created;
      userData.activities.prs_closed += user.activities.prs_closed;
      userData.activities.comments += user.activities.comments;

      // Store daily details
      userData.dailyDetails[dateIndex].commits += user.activities.commits;
      userData.dailyDetails[dateIndex].issues_created += user.activities.issues_created;
      userData.dailyDetails[dateIndex].issues_closed += user.activities.issues_closed;
      userData.dailyDetails[dateIndex].prs_created += user.activities.prs_created;
      userData.dailyDetails[dateIndex].prs_closed += user.activities.prs_closed;
      userData.dailyDetails[dateIndex].comments += user.activities.comments;

      // Calculate total activities for this day
      const dailyTotal =
        user.activities.commits +
        user.activities.issues_created +
        user.activities.issues_closed +
        user.activities.prs_created +
        user.activities.prs_closed +
        user.activities.comments;

      userData.dailyValues[dateIndex] = dailyTotal;

      // Add unique repositories
      if (user.repositories) {
        user.repositories.forEach((repo) => {
          if (!userData.repositories?.includes(repo)) {
            userData.repositories?.push(repo);
          }
        });
      }
    });
  });

  return Array.from(userMap.values());
}

export default function Timeline() {
  const [selectedTime, setSelectedTime] = useState<string>('Last 7 days');
  const [searchParams] = useSearchParams();
  const [userData, setUserData] = useState<UserActivityData[]>([]);
  const [dateLabels, setDateLabels] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missingDataPath, setMissingDataPath] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);
  const [selectedMembers, setSelectedMembers] = useState<string[]>([]);

  const selectedRepo = searchParams.get('repo');
    
  const handleTimeChange = (selected: string) => {
    setSelectedTime(selected);
  };

  // Fetch data when filters change
  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);
      setError(null);
      setMissingDataPath(null);
      setNotConfigured(false);
      try {
        const timeFilter = selectedTime === 'Last 7 days' ? 'last_7_days' : 'last_12_months';
        const repoFilter = selectedRepo || undefined;
        
        const timelineData = await TimelineExtraction.extractTimelineData(timeFilter, repoFilter);
        
        // Ensure we always have the correct number of days/months
        const expectedLength = selectedTime === 'Last 7 days' ? 7 : 12;
        
        // If we have data, fill in the missing days
        let completeTimelineData = [...timelineData];
        
        if (timelineData.length > 0 && timelineData.length < expectedLength) {
          // Get the first and last date
          const firstDate = new Date(timelineData[0].date + 'T00:00:00');
          const lastDate = new Date(timelineData[timelineData.length - 1].date + 'T00:00:00');
          
          // Create array with all expected dates
          const allDates: TimelineData[] = [];
          
          if (selectedTime === 'Last 7 days') {
            // For 7 days, fill from first to last + missing days
            const startDate = new Date(firstDate);
            for (let i = 0; i < expectedLength; i++) {
              const currentDate = new Date(startDate);
              currentDate.setDate(startDate.getDate() + i);
              const dateStr = `${currentDate.getFullYear()}-${String(currentDate.getMonth() + 1).padStart(2, '0')}-${String(currentDate.getDate()).padStart(2, '0')}`;
              
              // Check if it already exists in timelineData
              const existingData = timelineData.find(d => d.date === dateStr);
              if (existingData) {
                allDates.push(existingData);
              } else {
                // Add empty day
                allDates.push({
                  date: dateStr,
                  users: []
                });
              }
            }
          } else {
            // For 12 months, do the same
            const startDate = new Date(firstDate);
            for (let i = 0; i < expectedLength; i++) {
              const currentDate = new Date(startDate);
              currentDate.setMonth(startDate.getMonth() + i);
              const dateStr = `${currentDate.getFullYear()}-${String(currentDate.getMonth() + 1).padStart(2, '0')}-${String(currentDate.getDate()).padStart(2, '0')}`;
              
              const existingData = timelineData.find(d => d.date === dateStr);
              if (existingData) {
                allDates.push(existingData);
              } else {
                allDates.push({
                  date: dateStr,
                  users: []
                });
              }
            }
          }
          
          completeTimelineData = allDates;
        }
        
        // Extract the actual dates from JSON
        const dates = completeTimelineData.map(d => d.date);
        
        // Format dates for display
        const formattedDates = dates.map(dateStr => {
          const date = new Date(dateStr + 'T00:00:00');
          
          if (selectedTime === 'Last 7 days') {
            // Format: "Nov 11 (Mon)"
            const dayName = date.toLocaleDateString('en-US', { weekday: 'short' });
            const monthDay = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            return `${monthDay} (${dayName})`;
          } else {
            // Format for 12 months: "Nov 11 (November)" or just month if needed
            const monthName = date.toLocaleDateString('en-US', { month: 'long' });
            const monthDay = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            return `${monthDay} (${monthName})`;
          }
        });
        setDateLabels(formattedDates);

        const transformedData = transformToUserActivity(completeTimelineData);
        
        setUserData(transformedData);
      } catch (error) {
        if (isDataNotFoundError(error)) {
          setMissingDataPath(error.path);
        } else if (isDataUnconfiguredError(error)) {
          setNotConfigured(true);
        } else {
          console.error('Error fetching timeline data:', error);
          setError(error instanceof Error ? error.message : String(error));
        }
        setUserData([]);
        setDateLabels([]);
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, [selectedTime, selectedRepo]);

  // Extract list of unique members
  const availableMembers = useMemo(() => {
    return userData.map(user => user.name).sort();
  }, [userData]);

  // Filter userData based on selected members
  const filteredUserData = useMemo(() => {
    if (selectedMembers.length === 0) return userData;
    return userData.filter(user => selectedMembers.includes(user.name));
  }, [userData, selectedMembers]);

  return (
    <DashboardLayout currentPage="overview" currentSubPage="timeline">
      <div className="w-full h-full -mx-8 -my-8 px-8 py-8">
        <div className="space-y-4 mt-30">
          <div>
            <h1 className="text-3xl font-bold text-white mb-2">Activity Timeline</h1>
            <p className="text-slate-400 text-sm">
              Timeline of <strong>general</strong> repository events that happened during the <strong>past week or year</strong>. Represents the activities that team members made in <strong>any</strong> repository inside the organization
            </p>
          </div>
          {/* Charts Grid */}
          <div className="w-[1655px]">
            <div
              className="border rounded-lg h-170 w-full overflow-hidden flex flex-col"
              style={{ backgroundColor: '#222222', borderColor: '#333333' }}
            >

              

                  {/* Timeline Filter */}
                <div className="h-auto flex flex-col sm:flex-row sm:flex-wrap sm:gap-6 px-3 mb-2 mt-2">
                    <div className="ml-5">

                      <Filter
                        title="Date Range"
                        content={[
                          'Last 7 days',
                          'Last 12 months',
                        ]}
                        value={selectedTime}
                        sendSelectedValue={handleTimeChange}
                      />
                    </div>
                  
                
                  <div className="ml-3 border-l-2"
                  style={{ backgroundColor: '#222222', borderColor: '#333333' }}
                  >
                    <div className="ml-5">
                      {/* Member Filter */}
                      <MemberFilter
                        members={availableMembers}
                        selectedMembers={selectedMembers}
                        onMemberChange={setSelectedMembers}
                      />
                    </div>
                  </div>
                </div>
              
              


              <div 
                style={{ 
                  backgroundColor: '#1a1a1a', 
                  overflowX: 'auto',
                  overflowY: 'auto',
                  flex: 1,
                  padding: '16px'
                }}
              >
                {isLoading ? (
                  <div className="text-center text-slate-400 py-8">Loading data...</div>
                ) : notConfigured ? (
                  <DataNotConfigured />
                ) : missingDataPath ? (
                  <DataNotGenerated path={missingDataPath} />
                ) : error ? (
                  <div className="text-center text-red-400 py-8" role="alert">
                    Error loading data: {error}
                  </div>
                ) : filteredUserData.length === 0 ? (
                  <div className="text-center text-slate-400 py-8">No data available</div>
                ) : (
                  <div style={{ display: 'inline-block', minWidth: 'fit-content' }}>
                    <CalendarHeatmap
                      userData={filteredUserData}
                      mode={selectedTime === 'Last 7 days' ? 'weekly' : 'monthly'}
                      dateLabels={dateLabels}
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </DashboardLayout>
  );
}
