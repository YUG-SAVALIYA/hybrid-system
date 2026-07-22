import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, Fragment } from "react";
import { runManager, RunState, FlowState } from "../services/runManager";
import {
  DiscoveryApiError,
  DiscoveryGroupResult,
  DiscoveryHorizon,
  DiscoveryResult,
  DiscoveryStageResult,
  DiscoveryStageStatus,
  DiscoveryStockResult,
  getDiscoveryResult,
} from "../api/discovery";
import { useParams, useNavigate } from "react-router-dom";
import { ScoreCell, ScoreExplanationBanner } from "../components/ExplanationBanner";

type WarningItem = {
  code: string;
  context: string;
  message: string;
};

type ProcessLogItem = {
  id: string;
  label: string;
  status: string;
  source: string;
  timestamp?: string | null;
  detail?: string | null;
};

const HORIZONS: Array<{ key: DiscoveryHorizon; label: string; desc: string }> = [
  { key: "SHORT", label: "Short Term", desc: "1 - 3 Months" },
  { key: "MID", label: "Mid Term", desc: "3 - 12 Months" },
  { key: "LONG", label: "Long Term", desc: "1 - 3 Years" },
];

const STAGES: Array<{ key: string; label: string; source: "preparation" | "execution" }> = [
  { key: "UNIVERSE_SNAPSHOT", label: "Universe Snapshot", source: "preparation" },
  { key: "COMPANY_TECHNICAL", label: "Technical Analysis", source: "preparation" },
  { key: "COMPANY_FUNDAMENTAL", label: "Fundamental Analysis", source: "preparation" },
  { key: "UPSTREAM_VALIDATION", label: "Validation", source: "preparation" },
  { key: "MACRO_SEARCH", label: "Macro Search", source: "execution" },
  { key: "MACRO_FILTER", label: "Macro Filter", source: "execution" },
  { key: "SECTOR_SELECTION", label: "Find Best Sector", source: "execution" },
  { key: "INDUSTRY_SELECTION", label: "Find Best Industry", source: "execution" },
  { key: "BASIC_INDUSTRY_SELECTION", label: "Find Best Basic Industry", source: "execution" },
  { key: "STOCK_SELECTION", label: "Find Best Stocks", source: "execution" },
];

const RUN_ID_PATTERN = /^[A-Za-z0-9_-]{0,128}$/;

function displayError(error: DiscoveryApiError): string {
  if (error.code === "DISCOVERY_RUN_NOT_FOUND") return "Discovery run was not found.";
  if (error.code === "DISCOVERY_RUN_ALREADY_RUNNING") return "This discovery run is already being processed.";
  if (error.code === "BENCHMARK_DATA_UNAVAILABLE") {
    return "NIFTY 500 benchmark data is unavailable. Import genuine NIFTY 500 benchmark candles before running discovery.";
  }
  return error.message || "The discovery request could not be completed.";
}

function warningMessage(code: string) {
  if (code === "BENCHMARK_DATA_UNAVAILABLE") {
    return "NIFTY 500 benchmark data is unavailable. Import genuine NIFTY 500 benchmark candles before running discovery.";
  }
  return code.replace(/_/g, " ").toLowerCase();
}

function finiteScore(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value);
}

function stageStatus(stage?: DiscoveryStageResult): DiscoveryStageStatus {
  return stage?.status || "PENDING";
}

function collectStageWarnings(
  result: DiscoveryResult | null,
  preparation: Record<string, DiscoveryStageResult>
): WarningItem[] {
  const items: WarningItem[] = [];
  const seen = new Set<string>();
  const add = (code: string, context: string) => {
    const key = `${code}:${context}`;
    if (!code || seen.has(key)) return;
    seen.add(key);
    items.push({ code, context, message: warningMessage(code) });
  };
  result?.warnings.forEach((code) => add(code, "Run"));
  Object.entries(preparation).forEach(([stage, value]) => {
    value.warnings?.forEach((code) => add(code, stage));
  });
  return items;
}

type HorizonView = {
  sectors: DiscoveryGroupResult[];
  industries: DiscoveryGroupResult[];
  basicIndustries: DiscoveryGroupResult[];
  stocks: DiscoveryStockResult[];
  warnings: string[];
};

function buildHorizonView(result: DiscoveryResult | null, key: DiscoveryHorizon): HorizonView {
  const horizon = result?.horizons[key];
  const view: HorizonView = { sectors: [], industries: [], basicIndustries: [], stocks: [], warnings: [] };
  if (!horizon) return view;

  view.sectors = horizon.sectors || [];
  view.industries = horizon.industries || [];
  view.basicIndustries = horizon.basic_industries || [];
  view.stocks = horizon.stocks || [];
  view.warnings = horizon.warnings || [];
  return view;
}

function groupEmptyMessage(horizon: DiscoveryHorizon, result: DiscoveryResult | null, view: HorizonView, flowState: FlowState) {
  if (["CREATING_RUN", "PREPARING_DATA", "EXECUTING_DISCOVERY", "LOADING_RESULT"].includes(flowState)) {
    return "Pipeline is currently running. Please wait for automated scoring results...";
  }
  const data = result?.horizons[horizon];
  if (!data || data.status === "PENDING") return "Sector selection analysis has not completed for this horizon.";
  return null;
}

export function DiscoveryPage() {
  const { runId: routeRunId, tab: routeTab } = useParams();
  const navigate = useNavigate();
  const runId = routeRunId === "new" ? "" : (routeRunId || "");
  
  const [runState, setRunState] = useState<RunState>(runManager.getState());
  const { flowState, activeRunId, error, result, preparationStages } = runState;
  
  useEffect(() => {
    const unsubscribe = runManager.subscribe((state) => {
      setRunState({ ...state });
    });
    return () => { unsubscribe(); };
  }, []);

  const [customRunId, setCustomRunId] = useState("");
  const [existingRunId, setExistingRunId] = useState(runId || "");
  const [resumeExisting, setResumeExisting] = useState(true);
  const [forceRestartPreparation, setForceRestartPreparation] = useState<boolean>(false);
  const [forceRestartExecution, setForceRestartExecution] = useState<boolean>(false);

  const [activeHorizon, setActiveHorizon] = useState<DiscoveryHorizon>("SHORT");
  const [runHorizon, setRunHorizon] = useState<DiscoveryHorizon>("SHORT");
  const activeViewTab = routeTab ? routeTab.toUpperCase() : (runId ? "SECTORS" : undefined);
  
  const abortRef = useRef<AbortController | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollingRequestRef = useRef(false);

  const isBusy = ["CREATING_RUN", "PREPARING_DATA", "EXECUTING_DISCOVERY", "LOADING_RESULT"].includes(flowState);
  const runIdValid = RUN_ID_PATTERN.test(customRunId);
  const existingRunIdValid = RUN_ID_PATTERN.test(existingRunId);
  const safeError = error ? displayError(error) : null;

  const selectedHorizonView = useMemo(() => buildHorizonView(result, activeHorizon), [activeHorizon, result]);
  const emptyMessage = groupEmptyMessage(activeHorizon, result, selectedHorizonView, flowState);

  useEffect(() => {
    if (runId && runId !== activeRunId && flowState !== "LOADING_RESULT") {
      runManager.loadResult(runId).catch(console.error);
    }
  }, [runId, activeRunId, flowState]);

  const clearPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startDiscovery = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!runIdValid) return;
    await runManager.startRun(
      customRunId,
      runHorizon,
      resumeExisting,
      forceRestartPreparation,
      forceRestartExecution,
      (runId) => navigate(`/discovery/${runId}`)
    );
  };

  const loadExisting = async () => {
    if (!existingRunIdValid || !existingRunId.trim()) return;
    try {
      const res = await runManager.loadResult(existingRunId.trim());
      if (res?.run_id) {
        navigate(`/discovery/${res.run_id}`);
      }
    } catch (err: any) {};
  };

  const resumePreparation = async () => {
    await runManager.resumePreparation(forceRestartPreparation);
  };

  const resumeExecution = async () => {
    await runManager.resumeExecution(runHorizon, forceRestartExecution);
  };

  useEffect(() => {
    clearPolling();
    if (!activeRunId || result?.status !== "RUNNING") return;
    
    const poll = async () => {
      if (pollingRequestRef.current) return;
      pollingRequestRef.current = true;
      try {
        const loaded = await getDiscoveryResult(activeRunId);
        if (loaded.status !== "RUNNING") {
          clearPolling();
        } else {
          pollRef.current = setTimeout(poll, 5000);
        }
      } catch {
        clearPolling();
      } finally {
        pollingRequestRef.current = false;
      }
    };
    pollRef.current = setTimeout(poll, 5000);
    return clearPolling;
  }, [activeRunId, clearPolling, result?.status]);

  return (
    <main className="discovery-shell">
      {!runId && (
        <header className="dashboard-hero" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <p className="eyebrow" style={{ color: "var(--text-muted)", fontSize: "0.85rem", textTransform: "uppercase" }}>Pipeline Setup</p>
            <h1 style={{ margin: "4px 0 0 0" }}>Market Discovery Pipeline</h1>
          </div>
          <span className={`badge ${flowState === 'COMPLETED' ? 'completed' : flowState.includes('FAIL') ? 'error' : flowState === 'IDLE' ? 'pending' : 'running'}`}>
            {flowState.replace(/_/g, " ")}
          </span>
        </header>
      )}

      {!runId && (
        <section className="dashboard-grid">
          <form className="panel run-panel" onSubmit={startDiscovery}>
            <div className="panel-title">
              <h2>Launch New Discovery Run</h2>
              {isBusy && <span className="spinner-label" style={{ color: "var(--warning)", fontSize: "0.85rem" }}>⚡ Processing pipeline...</span>}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <label style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "0.9rem", fontWeight: 600 }}>
                Target Investment Horizon
                <select value={runHorizon} onChange={(e) => setRunHorizon(e.target.value as DiscoveryHorizon)}>
                  {HORIZONS.map((h) => (
                    <option key={h.key} value={h.key}>
                      {h.label} ({h.desc})
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "0.9rem", fontWeight: 600 }}>
                Custom Run ID (Optional)
                <input
                  type="text"
                  placeholder="e.g. Q3-tech-analysis"
                  value={customRunId}
                  onChange={(e) => setCustomRunId(e.target.value)}
                />
              </label>

              <button type="submit" className="primary" disabled={isBusy} style={{ marginTop: "8px" }}>
                🚀 Start Pipeline Execution
              </button>
            </div>
          </form>

          <div className="panel load-panel">
            <div className="panel-title">
              <h2>Load Existing Run ID</h2>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <label style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "0.9rem", fontWeight: 600 }}>
                Run ID
                <input
                  type="text"
                  placeholder="Enter previous run ID..."
                  value={existingRunId}
                  onChange={(e) => setExistingRunId(e.target.value)}
                />
              </label>
              <button type="button" className="secondary" disabled={isBusy || !existingRunId.trim()} onClick={loadExisting}>
                📂 Load Run Data
              </button>
            </div>
          </div>
        </section>
      )}

      {runId && (
        <section className="panel results-panel">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "16px", marginBottom: "20px" }}>
            <div>
              <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Active Run Analysis</div>
              <h2 style={{ fontSize: "1.5rem" }}>Run ID: {runId}</h2>
            </div>

            {/* Investment Horizon Switcher */}
            <div style={{ display: "flex", alignItems: "center", gap: "8px", background: "#18181b", padding: "4px", borderRadius: "10px", border: "1px solid var(--panel-border)" }}>
              <span style={{ fontSize: "0.8rem", color: "var(--text-muted)", padding: "0 8px", fontWeight: 600 }}>Horizon:</span>
              {HORIZONS.map((h) => (
                <button
                  key={h.key}
                  type="button"
                  className={activeHorizon === h.key ? "primary" : "secondary"}
                  onClick={() => setActiveHorizon(h.key)}
                  style={{ minHeight: "32px", padding: "0 12px", fontSize: "0.82rem" }}
                  title={`${h.label} (${h.desc})`}
                >
                  {h.label}
                </button>
              ))}
            </div>
          </div>

          <ScoreExplanationBanner />

          {emptyMessage ? (
            <div className="empty-state">{emptyMessage}</div>
          ) : (
            <div className="result-content">
              {activeViewTab === "SECTORS" && (
                <GroupTable
                  title="Sectors"
                  groups={selectedHorizonView.sectors}
                  onRowClick={(name, ps, pi) =>
                    navigate(`/discovery/${activeRunId}/group/SECTOR/${encodeURIComponent(name)}?parentSector=${encodeURIComponent(ps)}&parentIndustry=${encodeURIComponent(pi)}&horizon=${activeHorizon}`)
                  }
                />
              )}
              {activeViewTab === "INDUSTRIES" && (
                <GroupTable
                  title="Industries"
                  groups={selectedHorizonView.industries}
                  showParentSector={true}
                  onRowClick={(name, ps, pi) =>
                    navigate(`/discovery/${activeRunId}/group/INDUSTRY/${encodeURIComponent(name)}?parentSector=${encodeURIComponent(ps)}&parentIndustry=${encodeURIComponent(pi)}&horizon=${activeHorizon}`)
                  }
                />
              )}
              {activeViewTab === "BASIC_INDUSTRIES" && (
                <GroupTable
                  title="Basic Industries"
                  groups={selectedHorizonView.basicIndustries}
                  showParentSector={true}
                  showParentIndustry={true}
                  onRowClick={(name, ps, pi) =>
                    navigate(`/discovery/${activeRunId}/group/BASIC_INDUSTRY/${encodeURIComponent(name)}?parentSector=${encodeURIComponent(ps)}&parentIndustry=${encodeURIComponent(pi)}&horizon=${activeHorizon}`)
                  }
                />
              )}
              {activeViewTab === "STOCKS" && activeRunId && (
                <StocksTable
                  runId={activeRunId}
                  horizon={activeHorizon}
                  stocks={selectedHorizonView.stocks}
                  onStockSelect={(stock) => navigate(`/discovery/${activeRunId}/stock/${encodeURIComponent(stock.symbol)}?horizon=${stock.horizon}`)}
                />
              )}
            </div>
          )}
        </section>
      )}
    </main>
  );
}

function GroupTable({
  title,
  groups,
  showParentSector = false,
  showParentIndustry = false,
  onRowClick,
}: {
  title: string;
  groups: DiscoveryGroupResult[];
  showParentSector?: boolean;
  showParentIndustry?: boolean;
  onRowClick?: (name: string, parentSector: string, parentIndustry: string) => void;
}) {
  const [filter, setFilter] = useState("");

  if (!groups.length) return <div className="empty-state">No {title.toLowerCase()} found for this horizon.</div>;

  const filtered = groups.filter(
    (g) =>
      g.name.toLowerCase().includes(filter.toLowerCase()) ||
      (g.parent_sector && g.parent_sector.toLowerCase().includes(filter.toLowerCase())) ||
      (g.parent_industry && g.parent_industry.toLowerCase().includes(filter.toLowerCase()))
  );

  return (
    <div>
      <div className="table-filter-bar">
        <div className="search-input-wrap">
          <span className="search-icon">🔍</span>
          <input
            type="text"
            placeholder={`Search ${title.toLowerCase()} by name...`}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
          Showing <strong>{filtered.length}</strong> of <strong>{groups.length}</strong> {title.toLowerCase()}
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <caption>Top Ranked {title}</caption>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Name</th>
              {showParentSector && <th>Parent Sector</th>}
              {showParentIndustry && <th>Parent Industry</th>}
              <th>Technical Score (0-100)</th>
              <th>Fundamental Score (0-100)</th>
              <th>Macro Score (0-100)</th>
              <th>Final Score (0-100)</th>
              <th>Stocks Analyzed</th>
              <th>Data Coverage %</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((group) => (
              <tr
                key={`${group.rank}-${group.name}`}
                onClick={() => onRowClick && onRowClick(group.name, group.parent_sector || "", group.parent_industry || "")}
                className={(onRowClick ? "clickable-row" : "") + (group.selected ? " selected-row" : "")}
              >
                <td>
                  <span className="run-card-rank">#{group.rank || "-"}</span>
                </td>
                <td style={{ fontWeight: 600 }}>{group.name}</td>
                {showParentSector && <td>{group.parent_sector || "-"}</td>}
                {showParentIndustry && <td>{group.parent_industry || "-"}</td>}
                <td>
                  <ScoreCell score={group.technical_score} />
                </td>
                <td>
                  <ScoreCell score={group.fundamental_score} />
                </td>
                <td>
                  <ScoreCell score={group.macro_score} />
                </td>
                <td>
                  <ScoreCell score={group.final_score} />
                </td>
                <td style={{ fontWeight: 600 }}>{group.constituent_count || "-"} stocks</td>
                <td>{finiteScore(group.coverage_pct) ? `${group.coverage_pct?.toFixed(1)}%` : "-"}</td>
                <td>
                  <span className={`badge ${group.status?.toLowerCase()}`}>{group.status || "-"}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StocksTable({
  runId,
  horizon,
  stocks,
  onStockSelect,
}: {
  runId: string;
  horizon: string;
  stocks: DiscoveryStockResult[];
  onStockSelect?: (stock: { symbol: string; horizon: string }) => void;
}) {
  const [filter, setFilter] = useState("");

  if (!stocks.length) return <div className="empty-state">No stocks selected for this horizon.</div>;

  const sorted = [...stocks].sort((a, b) => (a.rank || 9999) - (b.rank || 9999));
  const filtered = sorted.filter((s) => s.symbol.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div>
      <div className="table-filter-bar">
        <div className="search-input-wrap">
          <span className="search-icon">🔍</span>
          <input
            type="text"
            placeholder="Search stock symbol (e.g. RELIANCE, TCS)..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
          Showing <strong>{filtered.length}</strong> of <strong>{stocks.length}</strong> stocks
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <caption>Selected Top Stock Recommendations</caption>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Symbol</th>
              <th>Final Composite Score (0-100)</th>
              <th>Technical Score (0-100)</th>
              <th>Fundamental Score (0-100)</th>
              <th>Data Coverage %</th>
              <th>Analysis Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((stock) => (
              <tr
                key={`${stock.rank}-${stock.symbol}`}
                onClick={() => onStockSelect?.({ symbol: stock.symbol, horizon })}
                className={(onStockSelect ? "clickable-row" : "") + (stock.selected ? " selected-row" : "")}
              >
                <td>
                  <span className="run-card-rank">#{stock.rank || "-"}</span>
                </td>
                <td style={{ fontWeight: 700, fontSize: "1.05rem", color: "#ffffff" }}>{stock.symbol}</td>
                <td>
                  <ScoreCell score={stock.final_score} />
                </td>
                <td>
                  <ScoreCell score={stock.technical_score} />
                </td>
                <td>
                  <ScoreCell score={stock.fundamental_score} />
                </td>
                <td>{finiteScore(stock.score_coverage_pct) ? `${stock.score_coverage_pct?.toFixed(1)}%` : "-"}</td>
                <td>
                  <span className={`badge ${stock.score_status?.toLowerCase()}`}>{stock.score_status || "-"}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
