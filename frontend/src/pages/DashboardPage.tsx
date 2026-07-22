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
        <div className="panel" style={{ padding: "14px 18px" }}>
          <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Total Discovery Runs</div>
          <div style={{ fontSize: "1.6rem", fontWeight: 700, color: "#ffffff", marginTop: "2px" }}>{runs.length}</div>
        </div>
        <div className="panel" style={{ padding: "14px 18px" }}>
          <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Completed Pipelines</div>
          <div style={{ fontSize: "1.6rem", fontWeight: 700, color: "var(--success)", marginTop: "2px" }}>{completedCount}</div>
        </div>
        <div className="panel" style={{ padding: "14px 18px" }}>
          <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Latest Pipeline Run</div>
          <div style={{ fontSize: "0.98rem", fontWeight: 600, color: "#ffffff", marginTop: "6px" }}>
            {runs[0]?.started_at ? new Date(runs[0].started_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "N/A"}
          </div>
        </div>
      </div>

      {/* Search & Filter Bar */}
      <div className="table-filter-bar" style={{ marginTop: "8px" }}>
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
      <div className="dashboard-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(400px, 1fr))", gap: "18px" }}>
        {filteredRuns.map((run) => {
          const isCompleted = run.status.startsWith("COMPLETED");
          const isFailed = run.status === "FAILED";
          const statusBadge = isFailed ? "error" : isCompleted ? "completed" : "pending";
          const hasData = run.top_sectors.length > 0 || run.top_industries.length > 0 || run.top_stocks.length > 0;
          const horizonText = run.horizon === "LONG" ? "Long Term (1M)" : run.horizon === "MID" ? "Mid Term (1W)" : "Short Term (1D)";

          return (
            <div key={run.run_id} className="run-card" style={{ padding: "18px 20px", overflow: "hidden" }}>
              <div>
                <div className="run-card-header" style={{ marginBottom: "14px" }}>
                  <div style={{ overflow: "hidden", paddingRight: "10px" }}>
                    <h3 style={{ fontSize: "1.05rem", margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={run.run_date || run.run_id}>
                      Run: {run.run_date || run.run_id}
                    </h3>
                    <div className="run-card-date" style={{ fontSize: "0.75rem", marginTop: "2px" }}>
                      {run.started_at ? new Date(run.started_at).toLocaleString() : run.run_id}
                    </div>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px", flexShrink: 0 }}>
                    <span className={`badge ${statusBadge}`}>
                      {run.status.replace(/_/g, " ")}
                    </span>
                    <span className="badge pending" style={{ fontSize: "0.68rem", padding: "2px 6px" }}>
                      🎯 {horizonText}
                    </span>
                  </div>
                </div>

                {!hasData ? (
                  <div style={{ padding: "20px 16px", background: "#18181b", borderRadius: "8px", border: "1px solid #27272a", fontSize: "0.85rem", color: "var(--text-muted)", textAlign: "center" }}>
                    ⚠️ No output selection data (Run incomplete or failed)
                  </div>
                ) : (
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", minWidth: 0 }}>
                    <SummaryGroup title="Sectors" items={run.top_sectors} badgeColor="#10b981" />
                    <SummaryGroup title="Industries" items={run.top_industries} badgeColor="#38bdf8" />
                    <SummaryGroup title="Basic Industries" items={run.top_basic_industries} badgeColor="#a855f7" />
                    <SummaryGroup title="Stocks" items={run.top_stocks} badgeColor="#f59e0b" />
                  </div>
                )}
              </div>

              <div style={{ display: "flex", gap: "10px", marginTop: "14px", paddingTop: "12px", borderTop: "1px solid var(--panel-border)" }}>
                <button
                  onClick={() => navigate(`/discovery/${run.run_id}/SECTORS`)}
                  className="primary"
                  style={{ flex: 1, minHeight: "36px", fontSize: "0.85rem" }}
                >
                  📊 View Sectors
                </button>
                <button
                  onClick={() => navigate(`/discovery/${run.run_id}/STOCKS`)}
                  className="secondary"
                  style={{ flex: 1, minHeight: "36px", fontSize: "0.85rem" }}
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

function SummaryGroup({ title, items, badgeColor }: { title: string; items: { name: string; rank?: number | null }[]; badgeColor?: string }) {
  if (!items || items.length === 0) return null;
  
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "5px", minWidth: 0 }}>
      <h4 style={{ fontSize: "0.72rem", margin: 0, letterSpacing: "0.06em" }}>{title}</h4>
      <div style={{ display: "flex", flexDirection: "column", gap: "4px", minWidth: 0 }}>
        {items.slice(0, 3).map((item, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              fontSize: "0.78rem",
              background: "#18181b",
              padding: "4px 8px",
              borderRadius: "6px",
              border: "1px solid #27272a",
              overflow: "hidden",
              minWidth: 0,
            }}
            title={item.name}
          >
            <span
              style={{
                fontSize: "0.68rem",
                fontWeight: 700,
                background: badgeColor || "#27272a",
                color: "#000000",
                padding: "1px 5px",
                borderRadius: "3px",
                flexShrink: 0,
              }}
            >
              #{item.rank || i + 1}
            </span>
            <span style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "#f4f4f5", minWidth: 0 }}>
              {item.name}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
