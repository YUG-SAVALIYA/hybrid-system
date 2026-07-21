import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, Fragment } from "react";
import { runManager, RunState, FlowState } from "../services/runManager";
import {
  createDiscoveryRun,
  DiscoveryApiError,
  DiscoveryApiException,
  DiscoveryGroupResult,
  DiscoveryHorizon,
  DiscoveryResult,
  DiscoveryStageResult,
  DiscoveryStageStatus,
  DiscoveryStockResult,
  executeDiscoveryRun,
  getDiscoveryResult,
  prepareDiscoveryRun,
} from "../api/discovery";
import { useParams, useNavigate } from "react-router-dom";

interface Constituent {
  symbol: string;
  name: string;
  sector: string;
  industry: string;
  basic_industry: string;
  technical_score: number | null;
  technical_status: string | null;
  company_return?: number | null;
  benchmark_return?: number | null;
  tech_details?: any;
  fundamental_score: number | null;
  fundamental_status: string | null;
  fund_details?: any;
  market_cap: number | null;
}



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

const HORIZONS: Array<{ key: DiscoveryHorizon; label: string }> = [
  { key: "SHORT", label: "Short Term" },
  { key: "MID", label: "Mid Term" },
  { key: "LONG", label: "Long Term" },
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

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function statusFromResult(status?: string): FlowState {
  if (status === "COMPLETED_WITH_WARNINGS") return "COMPLETED_WITH_WARNINGS";
  if (status === "COMPLETED") return "COMPLETED";
  if (status === "FAILED") return "FAILED";
  if (status === "RUNNING") return "RUNNING";
  return "IDLE";
}

function displayError(error: DiscoveryApiError): string {
  if (error.code === "DISCOVERY_RUN_NOT_FOUND") return "Discovery run was not found.";
  if (error.code === "DISCOVERY_RUN_ALREADY_RUNNING") return "This discovery run is already being processed.";
  if (error.code === "BENCHMARK_DATA_UNAVAILABLE") {
    return "NIFTY 500 benchmark data is unavailable. Import genuine NIFTY 500 benchmark candles before running discovery.";
  }
  if (error.code === "PARALLEL_AUTHENTICATION_FAILED" || /PARALLEL_API_KEY/i.test(error.message)) {
    return "Parallel.ai is not configured. Add PARALLEL_API_KEY to the backend environment.";
  }
  if (
    [
      "INVALID_DISCOVERY_RUN_ID",
      "INVALID_DISCOVERY_AS_OF_DATE",
      "INVALID_DISCOVERY_PREPARATION_REQUEST",
      "DISCOVERY_RUN_AS_OF_DATE_UNAVAILABLE",
      "DISCOVERY_PREPARATION_SERVICE_UNAVAILABLE",
      "DISCOVERY_UPSTREAM_DATA_UNAVAILABLE",
    ].includes(error.code)
  ) {
    return error.message;
  }
  if (
    [
      "DISCOVERY_RUN_CREATION_FAILED",
      "DISCOVERY_PREPARATION_FAILED",
      "DISCOVERY_PIPELINE_EXECUTION_FAILED",
      "DISCOVERY_RESULT_UNAVAILABLE",
    ].includes(error.code)
  ) {
    return "The discovery request could not be completed. Check backend prerequisites and try again.";
  }
  return error.message || "The discovery request could not be completed.";
}

function warningMessage(code: string) {
  if (code === "BENCHMARK_DATA_UNAVAILABLE") {
    return "NIFTY 500 benchmark data is unavailable. Import genuine NIFTY 500 benchmark candles before running discovery.";
  }
  if (code === "PARALLEL_AUTHENTICATION_FAILED" || code === "PARALLEL_CATEGORY_SEARCH_FAILED") {
    return "Parallel.ai is not configured. Add PARALLEL_API_KEY to the backend environment.";
  }
  return code.replace(/_/g, " ").toLowerCase();
}

function finiteScore(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value);
}

function scoreText(score?: number | null) {
  if (!finiteScore(score)) return <span className="score-null">-</span>;
  const val = score!;
  let className = "score-mid";
  if (val >= 70) className = "score-high";
  else if (val < 40) className = "score-low";
  return <span className={className}>{val.toFixed(1)}</span>;
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
    Object.entries(value.horizons || {}).forEach(([horizon, horizonStage]) => {
      horizonStage?.warnings?.forEach((code) => add(code, `${stage} ${horizon}`));
    });
  });
  Object.entries(result?.stage_results || {}).forEach(([stage, value]) => {
    value.warnings?.forEach((code) => add(code, stage));
    Object.entries(value.horizons || {}).forEach(([horizon, horizonStage]) => {
      horizonStage?.warnings?.forEach((code) => add(code, `${stage} ${horizon}`));
    });
  });
  HORIZONS.forEach(({ key }) => {
    const horizon = result?.horizons[key];
    horizon?.warnings?.forEach((code: string) => add(code, key));
    horizon?.sectors?.forEach((g: DiscoveryGroupResult) => g.warnings?.forEach((code: string) => add(code, `${key} Sector`)));
    horizon?.industries?.forEach((g: DiscoveryGroupResult) => g.warnings?.forEach((code: string) => add(code, `${key} Industry`)));
    horizon?.basic_industries?.forEach((g: DiscoveryGroupResult) => g.warnings?.forEach((code: string) => add(code, `${key} Basic Industry`)));
    horizon?.stocks.forEach((stock: DiscoveryStockResult) =>
      stock.warnings?.forEach((code: string) => add(code, `${key} ${stock.symbol}`))
    );
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
    return "Pipeline is currently running. Please wait for results...";
  }
  const data = result?.horizons[horizon];
  if (!data || data.status === "PENDING") return "Sector selection has not completed for this horizon.";
  return null;
}

function buildProcessLog(
  flowState: FlowState,
  activeRunId: string | null,
  preparation: Record<string, DiscoveryStageResult>,
  result: DiscoveryResult | null,
  error: DiscoveryApiError | null
): ProcessLogItem[] {
  const items: ProcessLogItem[] = [];
  items.push({
    id: "flow",
    label: activeRunId ? `Run ${activeRunId}` : "Discovery flow",
    status: flowState,
    source: "Frontend",
    detail: error ? displayError(error) : null,
  });

  for (const stage of STAGES) {
    const sourceMap = stage.source === "preparation" ? preparation : result?.stage_results || {};
    const stageResult = sourceMap[stage.key];
    if (!stageResult && flowState === "IDLE") continue;
    const status = stageStatus(stageResult);
    if (!stageResult && status === "PENDING") continue;
    items.push({
      id: stage.key,
      label: stage.label,
      status: status.toLowerCase(),
      source: stage.source === "preparation" ? "Preparation" : "Discovery execution",
      timestamp: stageResult?.completed_at || stageResult?.started_at || null,
      detail: stageResult?.error_message || stageResult?.warnings?.join(", ") || null,
    });
    Object.entries(stageResult?.horizons || {}).forEach(([horizon, horizonResult]) => {
      items.push({
        id: `${stage.key}-${horizon}`,
        label: `${stage.label} - ${horizon}`,
        status: stageStatus(horizonResult).toLowerCase(),
        source: "Horizon",
        timestamp: horizonResult?.completed_at || horizonResult?.started_at || null,
        detail: horizonResult?.error_message || horizonResult?.warnings?.join(", ") || null,
      });
    });
  }

  return items;
}

export function DiscoveryPage() {
  const { runId: routeRunId, tab: routeTab } = useParams();
  const navigate = useNavigate();
  const runId = routeRunId || "";
  
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
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const [activeHorizon, setActiveHorizon] = useState<DiscoveryHorizon>("SHORT");
  const [runHorizon, setRunHorizon] = useState<DiscoveryHorizon>("SHORT");
  const activeViewTab = routeTab ? routeTab.toUpperCase() : (routeRunId ? "SECTORS" : undefined);
  
  const abortRef = useRef<AbortController | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollingRequestRef = useRef(false);

  const isBusy = ["CREATING_RUN", "PREPARING_DATA", "EXECUTING_DISCOVERY", "LOADING_RESULT"].includes(flowState);
  const runIdValid = RUN_ID_PATTERN.test(customRunId);
  const existingRunIdValid = RUN_ID_PATTERN.test(existingRunId);
  const safeError = error ? displayError(error) : null;

  const warnings = useMemo(
    () => collectStageWarnings(result, preparationStages),
    [result, preparationStages]
  );
  const processLog = useMemo(
    () => buildProcessLog(flowState, activeRunId, preparationStages, result, error),
    [activeRunId, error, flowState, preparationStages, result]
  );
  const selectedHorizonView = useMemo(() => buildHorizonView(result, activeHorizon), [activeHorizon, result]);
  const emptyMessage = groupEmptyMessage(activeHorizon, result, selectedHorizonView, flowState);

  // Log warnings and errors to console instead of displaying in UI
  useEffect(() => {
    if (safeError) console.error("Pipeline Error:", safeError);
  }, [safeError]);

  useEffect(() => {
    warnings.forEach((w) => console.warn(`[WARNING] ${w.code} (${w.context}): ${w.message}`));
  }, [warnings]);

  useEffect(() => {
    selectedHorizonView.warnings.forEach((w) => console.warn(`[HORIZON WARNING] ${w}`));
  }, [selectedHorizonView.warnings]);

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


  useEffect(() => {
    if (runId && runId !== runManager.getState().activeRunId) {
      runManager.loadResult(runId).catch(console.error);
    } else if (runId && runManager.getState().flowState === "IDLE") {
      runManager.loadResult(runId).catch(console.error);
    }
  }, [runId]);

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
        
        if (loaded.status === "RUNNING") {
          pollRef.current = setTimeout(poll, 5000);
        } else {
          
          clearPolling();
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

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      clearPolling();
    };
  }, [clearPolling]);

  return (
    <main className="discovery-shell">
      {!routeRunId && (
        <header className="dashboard-hero" style={{ gridTemplateColumns: 'none', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="hero-copy">
            <p className="eyebrow">Financial analytics</p>
            <h1 style={{ marginBottom: 0 }}>Sector Discovery</h1>
          </div>
          <span className={`badge ${flowState === 'COMPLETED' ? 'completed' : flowState.includes('FAIL') ? 'error' : flowState === 'IDLE' ? 'pending' : 'running'}`} aria-live="polite">
            {flowState.replace(/_/g, " ")}
          </span>
        </header>
      )}

      {!routeRunId && (
        <section className="dashboard-grid">
          {!runId && (
            <form className="panel run-panel" onSubmit={startDiscovery}>
              <div className="panel-title">
                <h2>Run Controls</h2>
                {isBusy && <span className="spinner-label">Working...</span>}
              </div>
              <label>
                Target Horizon
                <select
                  value={runHorizon}
                  onChange={(event) => setRunHorizon(event.target.value as DiscoveryHorizon)}
                >
                  <option value="SHORT">SHORT</option>
                  <option value="MID">MID</option>
                  <option value="LONG">LONG</option>
                </select>
              </label>
              <label>
                Custom Run ID
                <input
                  value={customRunId}
                  maxLength={128}
                  aria-invalid={!runIdValid}
                  placeholder="Optional"
                  onChange={(event) => setCustomRunId(event.target.value)}
                />
              </label>
              {!runIdValid && <p className="field-error">Use letters, numbers, hyphens, and underscores only.</p>}

              <button type="submit" disabled={isBusy || !runIdValid}>
                {isBusy ? "Discovery Running" : "Start Discovery"}
              </button>

              <details open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
                <summary>Advanced Options</summary>
                <div className="advanced-options">
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={resumeExisting}
                      onChange={(event) => setResumeExisting(event.target.checked)}
                    />
                    Resume existing run
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={forceRestartPreparation}
                      onChange={(event) => setForceRestartPreparation(event.target.checked)}
                    />
                    Force restart preparation
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={forceRestartExecution}
                      onChange={(event) => setForceRestartExecution(event.target.checked)}
                    />
                    Force restart discovery execution
                  </label>
                  <label>
                    Load Existing Run
                    <input
                      value={existingRunId}
                      maxLength={128}
                      aria-invalid={!existingRunIdValid}
                      onChange={(event) => setExistingRunId(event.target.value)}
                    />
                  </label>
                  <div className="button-row">
                    <button type="button" className="secondary" disabled={isBusy || !existingRunId.trim() || !existingRunIdValid} onClick={loadExisting}>
                      Load Result
                    </button>
                    <button type="button" className="secondary" disabled={isBusy || !activeRunId} onClick={resumePreparation}>
                      Resume Preparation
                    </button>
                    <button type="button" className="secondary" disabled={isBusy || !activeRunId} onClick={resumeExecution}>
                      Resume Execution
                    </button>
                  </div>
                </div>
              </details>
            </form>
          )}

          <section className="panel progress-panel" aria-label="Pipeline progress">
            <div className="panel-title">
              <h2>Pipeline Progress</h2>

            </div>
            <ol className="timeline-compact">
              {STAGES.map((stage) => {
                const source = stage.source === "preparation" ? preparationStages : result?.stage_results || {};
                const stageResult = source[stage.key];
                const status = stageStatus(stageResult);

                let title = stage.label;
                if (status === "FAILED" && stageResult?.error_message) {
                  title += ` - Error: ${stageResult.error_message}`;
                }

                return (
                  <li className={`compact-pill pill-${status.toLowerCase()}`} key={stage.key} title={title}>
                    <span className="pill-dot"></span>
                    <span className="pill-label">{stage.label}</span>
                  </li>
                );
              })}
            </ol>
          </section>
        </section>
      )}

      {routeRunId && (
        <section className="panel results-panel">
          <div className="panel-title">
            <h2>Horizon Results</h2>
          </div>
          {emptyMessage ? (
            <div className="empty-state">{emptyMessage}</div>
          ) : (
            <div className="result-content">
              {activeViewTab === "SECTORS" && <GroupTable title="Sectors" groups={selectedHorizonView.sectors} onRowClick={(name, ps, pi) => navigate(`/discovery/${activeRunId}/group/SECTOR/${encodeURIComponent(name)}?parentSector=${encodeURIComponent(ps)}&parentIndustry=${encodeURIComponent(pi)}&horizon=${activeHorizon}`)} />}
              {activeViewTab === "INDUSTRIES" && <GroupTable title="Industries" groups={selectedHorizonView.industries} showParentSector={true} onRowClick={(name, ps, pi) => navigate(`/discovery/${activeRunId}/group/INDUSTRY/${encodeURIComponent(name)}?parentSector=${encodeURIComponent(ps)}&parentIndustry=${encodeURIComponent(pi)}&horizon=${activeHorizon}`)} />}
              {activeViewTab === "BASIC_INDUSTRIES" && <GroupTable title="Basic Industries" groups={selectedHorizonView.basicIndustries} showParentSector={true} showParentIndustry={true} onRowClick={(name, ps, pi) => navigate(`/discovery/${activeRunId}/group/BASIC_INDUSTRY/${encodeURIComponent(name)}?parentSector=${encodeURIComponent(ps)}&parentIndustry=${encodeURIComponent(pi)}&horizon=${activeHorizon}`)} />}
              {activeViewTab === "STOCKS" && activeRunId && <StocksTable runId={activeRunId} horizon={activeHorizon} stocks={selectedHorizonView.stocks} onStockSelect={(stock) => navigate(`/discovery/${activeRunId}/stock/${encodeURIComponent(stock.symbol)}?horizon=${stock.horizon}`)} />}
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
  onRowClick
}: {
  title: string;
  groups: DiscoveryGroupResult[];
  showParentSector?: boolean;
  showParentIndustry?: boolean;
  onRowClick?: (name: string, parentSector: string, parentIndustry: string) => void;
}) {
  if (!groups.length) return <div className="empty-state">No {title.toLowerCase()} found.</div>;
  return (
    <div className="table-wrap">
      <table>
        <caption>{title}</caption>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Name</th>
            {showParentSector && <th>Parent Sector</th>}
            {showParentIndustry && <th>Parent Industry</th>}
            <th>Technical Score</th>
            <th>Fundamental Score</th>
            <th>Macro Score</th>
            <th>Final Score</th>
            <th>Constituents</th>
            <th>Coverage</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <tr
              key={`${group.rank}-${group.name}`}
              onClick={() => onRowClick && onRowClick(group.name, group.parent_sector || '', group.parent_industry || '')}
              style={onRowClick ? { cursor: 'pointer' } : {}}
              className={(onRowClick ? "clickable-row" : "") + (group.selected ? " selected-row" : "")}
            >
              <td>{group.rank || "-"}</td>
              <td>{group.name}</td>
              {showParentSector && <td>{group.parent_sector || "-"}</td>}
              {showParentIndustry && <td>{group.parent_industry || "-"}</td>}
              <td>{scoreText(group.technical_score)}</td>
              <td>{scoreText(group.fundamental_score)}</td>
              <td>{scoreText(group.macro_score)}</td>
              <td>{scoreText(group.final_score)}</td>
              <td>{group.constituent_count || "-"}</td>
              <td>{finiteScore(group.coverage_pct) ? `${group.coverage_pct?.toFixed(1)}%` : "-"}</td>
              <td><span className={`badge ${group.status?.toLowerCase()}`}>{group.status || "-"}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StocksTable({
  runId,
  horizon,
  stocks,
  onStockSelect
}: {
  runId: string;
  horizon: string;
  stocks: DiscoveryStockResult[];
  onStockSelect?: (stock: { symbol: string, horizon: string }) => void;
}) {
  if (!stocks.length) return <div className="empty-state">No stocks found.</div>;
  const sorted = [...stocks].sort((a, b) => (a.rank || 9999) - (b.rank || 9999));

  return (
    <div className="table-wrap">
      <table>
        <caption>Selected Stocks</caption>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Symbol</th>
            <th>Final Score</th>
            <th>Technical Score</th>
            <th>Fundamental Score</th>
            <th>Coverage</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((stock) => (
            <tr
              key={`${stock.rank}-${stock.symbol}`}
              onClick={() => onStockSelect?.({ symbol: stock.symbol, horizon })}
              style={onStockSelect ? { cursor: 'pointer' } : {}}
              className={(onStockSelect ? "clickable-row" : "") + (stock.selected ? " selected-row" : "")}
            >
              <td>{stock.rank || "-"}</td>
              <td>{stock.symbol}</td>
              <td>{scoreText(stock.final_score)}</td>
              <td>{scoreText(stock.technical_score)}</td>
              <td>{scoreText(stock.fundamental_score)}</td>
              <td>{finiteScore(stock.score_coverage_pct) ? `${stock.score_coverage_pct?.toFixed(1)}%` : "-"}</td>
              <td><span className={`badge ${stock.score_status?.toLowerCase()}`}>{stock.score_status || "-"}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
