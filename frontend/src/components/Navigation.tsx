export type TabName = "DASHBOARD" | "PIPELINE" | "SECTORS" | "INDUSTRIES" | "BASIC_INDUSTRIES" | "STOCKS";

import { Link, useLocation, useParams, matchPath } from "react-router-dom";

export function Navigation() {
  const location = useLocation();
  
  // Try to match if we have a runId in the URL
  const runMatch = matchPath("/discovery/:runId/*", location.pathname);
  const runId = runMatch?.params?.runId && runMatch.params.runId !== "new" ? runMatch.params.runId : null;
  const runSelected = !!runId;
  const tabs: { key: TabName; label: string }[] = [
    { key: "DASHBOARD", label: "Dashboard" },
    { key: "PIPELINE", label: "Run Pipeline" },
    { key: "SECTORS", label: "Sectors" },
    { key: "INDUSTRIES", label: "Industries" },
    { key: "BASIC_INDUSTRIES", label: "Basic Industries" },
    { key: "STOCKS", label: "Stocks" }
  ];

  return (
    <nav style={{ padding: '16px 20px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', display: 'flex', gap: '16px', alignItems: 'center' }}>
      <h1 style={{ margin: '0 24px 0 0', fontSize: '1.2rem', color: 'var(--accent)' }}>Discovery System</h1>
      <div className="tabs" role="tablist">
        <Link
          to="/"
          role="tab"
          className={`tab ${location.pathname === "/" ? "active" : ""}`}
        >
          Dashboard
        </Link>
        <Link
          to="/discovery/new"
          role="tab"
          className={`tab ${location.pathname === "/discovery/new" ? "active" : ""}`}
        >
          Run Pipeline
        </Link>
        <Link
          to={runId ? `/discovery/${runId}/sectors` : "#"}
          role="tab"
          className={`tab ${location.pathname.match(/^\/discovery\/[^\/]+(\/sectors)?$/) && runId ? "active" : ""}`}
          style={!runSelected ? { opacity: 0.5, cursor: 'not-allowed', pointerEvents: 'none' } : {}}
          title={!runSelected ? "Select a run from the Dashboard first" : ""}
        >
          Sectors
        </Link>
        <Link
          to={runId ? `/discovery/${runId}/industries` : "#"}
          role="tab"
          className={`tab ${location.pathname.match(/^\/discovery\/[^\/]+\/industries$/) && runId ? "active" : ""}`}
          style={!runSelected ? { opacity: 0.5, cursor: 'not-allowed', pointerEvents: 'none' } : {}}
        >
          Industries
        </Link>
        <Link
          to={runId ? `/discovery/${runId}/basic_industries` : "#"}
          role="tab"
          className={`tab ${location.pathname.match(/^\/discovery\/[^\/]+\/basic_industries$/) && runId ? "active" : ""}`}
          style={!runSelected ? { opacity: 0.5, cursor: 'not-allowed', pointerEvents: 'none' } : {}}
        >
          Basic Industries
        </Link>
        <Link
          to={runId ? `/discovery/${runId}/stocks` : "#"}
          role="tab"
          className={`tab ${location.pathname.match(/^\/discovery\/[^\/]+\/stocks$/) && runId ? "active" : ""}`}
          style={!runSelected ? { opacity: 0.5, cursor: 'not-allowed', pointerEvents: 'none' } : {}}
        >
          Stocks
        </Link>
      </div>
    </nav>
  );
}
