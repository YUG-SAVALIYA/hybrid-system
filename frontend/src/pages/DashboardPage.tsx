import { useEffect, useState } from "react";
import { getRecentDiscoveryRuns, DiscoveryRunSummary } from "../api/discovery";

export function DashboardPage({ onRunSelect, onNewRun }: { onRunSelect: (runId: string) => void, onNewRun: () => void }) {
  const [runs, setRuns] = useState<DiscoveryRunSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    getRecentDiscoveryRuns().then(data => {
      if (active) {
        setRuns(data);
        setLoading(false);
      }
    }).catch(err => {
      console.error(err);
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, []);

  if (loading) return <div className="empty-state">Loading dashboard...</div>;
  if (!runs.length) return <div className="empty-state">No discovery runs found.</div>;

  return (
    <div className="discovery-shell">
      <header className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <p className="eyebrow">Overview</p>
          <h1>Recent Discovery Runs</h1>
        </div>
        <button className="primary" onClick={onNewRun} style={{ padding: '10px 24px', fontSize: '1rem' }}>
          Run Pipeline
        </button>
      </header>

      <div className="dashboard-grid">
        {runs.map(run => {
          const statusClass = run.status === 'FAILED' ? 'failed' : run.status.startsWith('COMPLETED') ? 'completed' : '';
          
          return (
            <div key={run.run_id} className={`run-card ${statusClass}`}>
              <div className="run-card-header">
                <div>
                  <h3>Run: {run.run_date || run.started_at || run.run_id}</h3>
                  <div className="run-card-date">{run.started_at ? new Date(run.started_at).toLocaleString() : ''}</div>
                </div>
                <span className={`badge ${run.status.toLowerCase().replace(/_/g, '-')}`}>{run.status.replace(/_/g, ' ')}</span>
              </div>
              
              <div className="run-card-content">
                <SummaryList title="Top Sectors" items={run.top_sectors} />
                <SummaryList title="Top Industries" items={run.top_industries} />
                <SummaryList title="Top Basic Industries" items={run.top_basic_industries} />
                <SummaryList title="Top Stocks" items={run.top_stocks} />
              </div>

              <div className="run-card-actions">
                <button onClick={() => onRunSelect(run.run_id)} className="primary">More Details</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SummaryList({ title, items }: { title: string, items: { name: string, rank?: number | null }[] }) {
  return (
    <div className="run-card-section">
      <h4>{title}</h4>
      {items.length === 0 ? (
        <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>No selections</div>
      ) : (
        <ul className="run-card-list">
          {items.map((item, i) => (
            <li key={i}>
              {item.rank ? <span className="run-card-rank">{item.rank}</span> : null}
              <span>{item.name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
