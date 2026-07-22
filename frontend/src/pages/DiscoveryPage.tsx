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

const HORIZONS: Array<{ key: DiscoveryHorizon; label: string; desc: string }> = [
  { key: "SHORT", label: "Short Term", desc: "1 Day / 1D" },
  { key: "MID", label: "Mid Term", desc: "1 Week / 1W" },
  { key: "LONG", label: "Long Term", desc: "1 Month / 1M" },
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

function finiteScore(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value);
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
    return "Pipeline is currently running automated market analysis. Watch the stage tracker above for real-time progress...";
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

  const [activeHorizon, setActiveHorizon] = useState<DiscoveryHorizon>("SHORT");
  const [runHorizon, setRunHorizon] = useState<DiscoveryHorizon>("SHORT");
  const activeViewTab = routeTab ? routeTab.toUpperCase() : (runId ? "SECTORS" : undefined);
  
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollingRequestRef = useRef(false);

  const isBusy = ["CREATING_RUN", "PREPARING_DATA", "EXECUTING_DISCOVERY", "LOADING_RESULT"].includes(flowState);
  const runIdValid = RUN_ID_PATTERN.test(customRunId);
  const existingRunIdValid = RUN_ID_PATTERN.test(existingRunId);

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
      false,
      false,
      (newRunId) => navigate(`/discovery/${newRunId}`)
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

  useEffect(() => {
    if (result?.horizons) {
      const keys: DiscoveryHorizon[] = ["SHORT", "MID", "LONG"];
      const populated = keys.find((k) => (result.horizons[k]?.sectors && result.horizons[k].sectors.length > 0) || (result.horizons[k]?.stocks && result.horizons[k].stocks.length > 0));
      if (populated) setActiveHorizon(populated);
    }
  }, [result]);



  const showTracker = isBusy || flowState !== "IDLE" || !!runId || !!activeRunId;

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

      {/* Live Pipeline Progress Tracker Banner */}
      {showTracker && (
        <PipelineProgressTracker
          flowState={flowState}
          preparationStages={preparationStages}
          result={result}
        />
      )}

      {!runId && (
        <section className="dashboard-grid">
          <form className="panel run-panel" onSubmit={startDiscovery}>
            <div className="panel-title">
              <h2>Launch New Discovery Run</h2>
              {isBusy && <span className="spinner-label" style={{ color: "var(--warning)", fontSize: "0.85rem" }}>⚡ Pipeline in progress...</span>}
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

            {/* Fixed Investment Horizon Indicator */}
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span className="badge pending" style={{ fontSize: "0.85rem", padding: "6px 14px", background: "#18181b", border: "1px solid var(--panel-border)" }}>
                🎯 Target Horizon: <strong style={{ color: "#ffffff", marginLeft: "4px" }}>{activeHorizon === "LONG" ? "Long Term (1 Month / 1M)" : activeHorizon === "MID" ? "Mid Term (1 Week / 1W)" : "Short Term (1 Day / 1D)"}</strong>
              </span>
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

function PipelineProgressTracker({
  flowState,
  preparationStages,
  result,
}: {
  flowState: FlowState;
  preparationStages: Record<string, DiscoveryStageResult>;
  result: DiscoveryResult | null;
}) {
  const stageResultsMap = result?.stage_results || {};

  const computedStages = STAGES.map((stage) => {
    const sourceMap = stage.source === "preparation" ? preparationStages : stageResultsMap;
    const stageResult = sourceMap[stage.key];
    
    let status: string = stageResult?.status || "PENDING";
    
    if (status === "PENDING") {
      if (flowState === "PREPARING_DATA" && stage.source === "preparation") {
        const firstUncompletedPrep = STAGES.find(
          (s) => s.source === "preparation" && (!preparationStages[s.key] || preparationStages[s.key].status !== "COMPLETED")
        );
        if (firstUncompletedPrep?.key === stage.key) {
          status = "RUNNING";
        }
      } else if ((flowState === "EXECUTING_DISCOVERY" || flowState === "RUNNING") && stage.source === "execution") {
        const firstUncompletedExec = STAGES.find(
          (s) => s.source === "execution" && (!stageResultsMap[s.key] || stageResultsMap[s.key].status !== "COMPLETED")
        );
        if (firstUncompletedExec?.key === stage.key) {
          status = "RUNNING";
        }
      }
    }

    if (flowState === "COMPLETED" || flowState === "COMPLETED_WITH_WARNINGS") {
      if (status !== "FAILED") status = "COMPLETED";
    }

    return {
      ...stage,
      status,
      detail: stageResult?.error_message || stageResult?.warnings?.join(", ") || null,
    };
  });

  const completedCount = computedStages.filter((s) => s.status === "COMPLETED" || s.status === "COMPLETED_WITH_WARNINGS").length;
  const isRunning = ["CREATING_RUN", "PREPARING_DATA", "EXECUTING_DISCOVERY", "LOADING_RESULT", "RUNNING"].includes(flowState);
  
  let percentage = Math.round((completedCount / STAGES.length) * 100);
  if (flowState === "COMPLETED" || flowState === "COMPLETED_WITH_WARNINGS") percentage = 100;
  if (isRunning && percentage === 0) percentage = 10;

  const activeStage = computedStages.find((s) => s.status === "RUNNING") || computedStages.find((s) => s.status === "PENDING");

  return (
    <div className="pipeline-tracker-panel">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <h3 style={{ fontSize: "1.15rem", margin: 0 }}>
              {isRunning ? "⚡ Pipeline Execution in Progress" : "✅ Pipeline Execution Status"}
            </h3>
            <span className={`badge ${flowState === 'COMPLETED' ? 'completed' : flowState.includes('FAIL') ? 'error' : flowState === 'IDLE' ? 'pending' : 'running'}`}>
              {flowState.replace(/_/g, " ")}
            </span>
          </div>
          <div style={{ fontSize: "0.88rem", color: "var(--text-secondary)", marginTop: "4px" }}>
            {isRunning && activeStage ? (
              <span>Currently Processing: <strong style={{ color: "#38bdf8" }}>Step {completedCount + 1} of 10: {activeStage.label}</strong></span>
            ) : flowState === "COMPLETED" ? (
              <span style={{ color: "var(--success)" }}>All 10 pipeline analysis stages completed successfully!</span>
            ) : (
              <span>Stage Progress: {completedCount} of 10 stages completed</span>
            )}
          </div>
        </div>
        <div style={{ fontSize: "1.4rem", fontWeight: 700, color: "#ffffff" }}>
          {percentage}%
        </div>
      </div>

      {/* Progress Bar */}
      <div className="pipeline-progress-bar-bg">
        <div 
          className={`pipeline-progress-bar-fill ${isRunning ? 'running' : ''}`}
          style={{ width: `${percentage}%` }}
        />
      </div>

      {/* 10-Stage Steps Grid */}
      <div className="stage-step-grid">
        {computedStages.map((stage, idx) => {
          const isDone = stage.status === "COMPLETED" || stage.status === "COMPLETED_WITH_WARNINGS";
          const isExec = stage.status === "RUNNING";
          const isErr = stage.status === "FAILED";

          let icon = "⚪";
          let cardClass = "pending";
          if (isDone) {
            icon = "🟢";
            cardClass = "completed";
          } else if (isExec) {
            icon = "⚡";
            cardClass = "running";
          } else if (isErr) {
            icon = "🔴";
            cardClass = "failed";
          }

          return (
            <div key={stage.key} className={`stage-step-card ${cardClass}`} title={stage.detail || stage.label}>
              <span style={{ fontSize: "1rem" }}>{icon}</span>
              <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
                  Step {idx + 1}
                </span>
                <span style={{ fontWeight: isExec ? 700 : 500 }}>
                  {stage.label}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
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
