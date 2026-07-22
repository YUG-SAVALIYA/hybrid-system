import { useEffect, useState } from "react";
import { getRecentDiscoveryRuns, DiscoveryRunSummary } from "../api/discovery";
import { useNavigate } from "react-router-dom";
import { ScoreExplanationBanner } from "../components/ExplanationBanner";

export function DashboardPage() {
  const [runs, setRuns] = useState<DiscoveryRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;
    getRecentDiscoveryRuns()
      .then((data) => {
        if (active) {
          setRuns(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error(err);
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  if (loading) return <div className="empty-state">⚡ Loading Discovery Engine Dashboard...</div>;
  if (!runs.length) return <div className="empty-state">No discovery runs found. Run a new pipeline to generate market intelligence.</div>;

  const filteredRuns = runs.filter((r) => {
    const term = searchTerm.toLowerCase();
    return (
      r.run_id.toLowerCase().includes(term) ||
      (r.run_date && r.run_date.toLowerCase().includes(term)) ||
      r.top_sectors.some((s) => s.name.toLowerCase().includes(term)) ||
      r.top_stocks.some((s) => s.name.toLowerCase().includes(term))
    );
  });

  const completedCount = runs.filter((r) => r.status.startsWith("COMPLETED")).length;

  return (
    <div className="discovery-shell">
      {/* Top Header */}
      <header className="dashboard-hero" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "20px" }}>
        <div>
          <p className="eyebrow" style={{ color: "var(--text-muted)", fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>Market Intelligence Overview</p>
          <h1 style={{ margin: "4px 0 0 0" }}>Discovery Engine Dashboard</h1>
        </div>
        <button className="primary" onClick={() => navigate("/discovery/new")} style={{ padding: "10px 24px", fontSize: "0.95rem" }}>
          🚀 Start New Discovery Pipeline
        </button>
      </header>

      {/* Explanation Banner */}
      <ScoreExplanationBanner />

      {/* Stats Summary Bar */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "16px", marginBottom: "8px" }}>
        <div className="panel" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Total Discovery Runs</div>
          <div style={{ fontSize: "1.8rem", fontWeight: 700, color: "#ffffff", marginTop: "4px" }}>{runs.length}</div>
        </div>
        <div className="panel" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Completed Pipelines</div>
          <div style={{ fontSize: "1.8rem", fontWeight: 700, color: "var(--success)", marginTop: "4px" }}>{completedCount}</div>
        </div>
        <div className="panel" style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Latest Pipeline Run</div>
          <div style={{ fontSize: "1.05rem", fontWeight: 600, color: "#ffffff", marginTop: "8px" }}>
            {runs[0]?.started_at ? new Date(runs[0].started_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "N/A"}
          </div>
        </div>
      </div>

      {/* Search & Filter Bar */}
      <div className="table-filter-bar" style={{ marginTop: "12px" }}>
        <div className="search-input-wrap" style={{ maxWidth: "450px" }}>
          <span className="search-icon">🔍</span>
          <input
            type="text"
            placeholder="Filter runs by ID, sector, or stock symbol..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        <div style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
          Showing <strong>{filteredRuns.length}</strong> of <strong>{runs.length}</strong> runs
        </div>
      </div>

      {/* Runs Grid */}
      <div className="dashboard-grid">
        {filteredRuns.map((run) => {
          const isCompleted = run.status.startsWith("COMPLETED");
          const isFailed = run.status === "FAILED";
          const statusBadge = isFailed ? "error" : isCompleted ? "completed" : "pending";

          return (
            <div key={run.run_id} className="run-card">
              <div>
                <div className="run-card-header">
                  <div>
                    <h3 style={{ fontSize: "1.05rem" }}>Run: {run.run_date || run.run_id}</h3>
                    <div className="run-card-date">
                      {run.started_at ? new Date(run.started_at).toLocaleString() : run.run_id}
                    </div>
                  </div>
                  <span className={`badge ${statusBadge}`}>
                    {run.status.replace(/_/g, " ")}
                  </span>
                </div>

                <div className="run-card-content">
                  <SummaryList title="Top Rated Sectors" items={run.top_sectors} badgeColor="var(--success)" />
                  <SummaryList title="Top Industries" items={run.top_industries} badgeColor="#38bdf8" />
                  <SummaryList title="Top Basic Industries" items={run.top_basic_industries} badgeColor="#a855f7" />
                  <SummaryList title="Top Ranked Stocks" items={run.top_stocks} badgeColor="#f59e0b" />
                </div>
              </div>

              <div style={{ display: "flex", gap: "10px", marginTop: "16px", paddingTop: "14px", borderTop: "1px solid var(--panel-border)" }}>
                <button
                  onClick={() => navigate(`/discovery/${run.run_id}/SECTORS`)}
                  className="primary"
                  style={{ flex: 1 }}
                >
                  📊 View Sectors
                </button>
                <button
                  onClick={() => navigate(`/discovery/${run.run_id}/STOCKS`)}
                  className="secondary"
                  style={{ flex: 1 }}
                >
                  📈 View Stocks
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SummaryList({ title, items, badgeColor }: { title: string; items: { name: string; rank?: number | null }[]; badgeColor?: string }) {
  return (
    <div className="run-card-section">
      <h4>{title}</h4>
      {items.length === 0 ? (
        <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No data available for this run</div>
      ) : (
        <ul className="run-card-list">
          {items.slice(0, 3).map((item, i) => (
            <li key={i}>
              <span className="run-card-rank" style={badgeColor ? { background: badgeColor, color: "#000" } : {}}>
                #{item.rank || i + 1}
              </span>
              <span style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {item.name}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
