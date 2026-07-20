export type TabName = "DASHBOARD" | "PIPELINE" | "SECTORS" | "INDUSTRIES" | "BASIC_INDUSTRIES" | "STOCKS";

export function Navigation({
  activeTab,
  onTabChange,
  runSelected
}: {
  activeTab: TabName;
  onTabChange: (tab: TabName) => void;
  runSelected: boolean;
}) {
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
        {tabs.map(({ key, label }) => {
          const disabled = !runSelected && key !== "DASHBOARD" && key !== "PIPELINE";
          return (
            <button
              key={key}
              role="tab"
              type="button"
              aria-selected={activeTab === key}
              className={`tab ${activeTab === key ? "active" : ""}`}
              onClick={() => !disabled && onTabChange(key)}
              style={disabled ? { opacity: 0.5, cursor: 'not-allowed' } : {}}
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
