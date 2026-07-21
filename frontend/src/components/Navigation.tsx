export type TabName = "DASHBOARD" | "PIPELINE" | "SECTORS" | "INDUSTRIES" | "BASIC_INDUSTRIES" | "STOCKS";

import { Link, useLocation, useParams, matchPath, useNavigate } from "react-router-dom";

export function Navigation() {
  const location = useLocation();
  
  const runMatch = matchPath("/discovery/:runId/*", location.pathname);
  const runId = runMatch?.params?.runId && runMatch.params.runId !== "new" ? runMatch.params.runId : null;
  const runSelected = !!runId;

  const navigate = useNavigate();

  let activeTab: TabName = "DASHBOARD";
  if (location.pathname === "/new") {
    activeTab = "PIPELINE";
  } else if (location.pathname.includes("/SECTOR")) {
    activeTab = "SECTORS";
  } else if (location.pathname.includes("/INDUSTRY")) {
    activeTab = "INDUSTRIES";
  } else if (location.pathname.includes("/BASIC_INDUSTRY")) {
    activeTab = "BASIC_INDUSTRIES";
  } else if (location.pathname.includes("/STOCK")) {
    activeTab = "STOCKS";
  }

  const onTabChange = (key: TabName) => {
    if (key === "DASHBOARD") navigate("/");
    else if (key === "PIPELINE") navigate("/new");
    else if (runId && key === "SECTORS") navigate(`/discovery/${runId}/SECTOR`);
    else if (runId && key === "INDUSTRIES") navigate(`/discovery/${runId}/INDUSTRY`);
    else if (runId && key === "BASIC_INDUSTRIES") navigate(`/discovery/${runId}/BASIC_INDUSTRY`);
    else if (runId && key === "STOCKS") navigate(`/discovery/${runId}/STOCK`);
  };
  const tabs: { key: TabName; label: string }[] = [
    { key: "DASHBOARD", label: "Dashboard" },
    { key: "PIPELINE", label: "Run Pipeline" },
    { key: "SECTORS", label: "Sectors" },
    { key: "INDUSTRIES", label: "Industries" },
    { key: "BASIC_INDUSTRIES", label: "Basic Industries" },
    { key: "STOCKS", label: "Stocks" }
  ];

  return (
    <nav className="app-nav">
      <div className="nav-brand">
        <div className="brand-logo">📊</div>
        <div>
          <div className="brand-title">Discovery Engine</div>
          <div className="brand-subtitle">AI Powered Market Intelligence</div>
        </div>
      </div>

      <div className="nav-tabs">
        {tabs.map(({ key, label }) => {
          const disabled = !runSelected && key !== "DASHBOARD" && key !== "PIPELINE";
          return (
            <button
              key={key}
              type="button"
              className={`nav-tab ${activeTab === key ? "active" : ""}`}
              onClick={() => !disabled && onTabChange(key)}
              style={disabled ? { opacity: 0.45, cursor: 'not-allowed' } : {}}
              title={disabled ? "Select a run from the Dashboard first" : ""}
            >
              {label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
