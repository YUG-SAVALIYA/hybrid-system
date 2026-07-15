import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, Fragment } from "react";
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

type FlowState =
  | "IDLE"
  | "CREATING_RUN"
  | "PREPARING_DATA"
  | "EXECUTING_DISCOVERY"
  | "LOADING_RESULT"
  | "COMPLETED"
  | "COMPLETED_WITH_WARNINGS"
  | "FAILED";

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

function groupEmptyMessage(horizon: DiscoveryHorizon, result: DiscoveryResult | null, view: HorizonView) {
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
  const [flowState, setFlowState] = useState<FlowState>("IDLE");
  const [customRunId, setCustomRunId] = useState("");
  const [existingRunId, setExistingRunId] = useState("");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [resumeExisting, setResumeExisting] = useState(true);
  const [forceRestartPreparation, setForceRestartPreparation] = useState<boolean>(false);
  const [forceRestartExecution, setForceRestartExecution] = useState<boolean>(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  
  const [selectedGroup, setSelectedGroup] = useState<{type: string, name: string, parentSector: string, parentIndustry: string} | null>(null);

  const [activeHorizon, setActiveHorizon] = useState<DiscoveryHorizon>("SHORT");
  const [runHorizon, setRunHorizon] = useState<DiscoveryHorizon>("SHORT");
  const [activeViewTab, setActiveViewTab] = useState<"SECTORS" | "INDUSTRIES" | "BASIC_INDUSTRIES" | "STOCKS">("SECTORS");
  const [result, setResult] = useState<DiscoveryResult | null>(null);
  const [preparationStages, setPreparationStages] = useState<Record<string, DiscoveryStageResult>>({});
  const [error, setError] = useState<DiscoveryApiError | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const inFlightRef = useRef(false);
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
  const emptyMessage = groupEmptyMessage(activeHorizon, result, selectedHorizonView);

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

  const clearPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
    setIsPolling(false);
  }, []);

  const loadResult = useCallback(
    async (runId: string, signal?: AbortSignal) => {
      setFlowState("LOADING_RESULT");
      const loaded = await getDiscoveryResult(runId, signal);
      setResult(loaded);
      setActiveRunId(loaded.run_id);
      setError(loaded.error || null);
      setFlowState(statusFromResult(loaded.status));
      return loaded;
    },
    []
  );

  const handleApiError = useCallback(
    async (apiError: DiscoveryApiError, runId?: string | null, shouldLoadResult = false) => {
      setError(apiError);
      setFlowState("FAILED");
      if (shouldLoadResult && runId) {
        try {
          await loadResult(runId);
        } catch {
          setFlowState("FAILED");
        }
      }
    },
    [loadResult]
  );

  const startDiscovery = async (event?: FormEvent) => {
    event?.preventDefault();
    if (inFlightRef.current || !runIdValid) return;
    inFlightRef.current = true;
    clearPolling();
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setResult(null);
    setPreparationStages({});
    try {
      setFlowState("CREATING_RUN");
      const created = await createDiscoveryRun(
        { run_id: customRunId.trim() || null, as_of_date: null },
        controller.signal
      );
      setActiveRunId(created.run_id);
      setFlowState("PREPARING_DATA");
      const preparation = await prepareDiscoveryRun(
        created.run_id,
        { resume: resumeExisting, force_restart: forceRestartPreparation },
        controller.signal
      );
      setPreparationStages(preparation.stage_results);
      if (preparation.error || preparation.status === "FAILED") {
        await handleApiError(
          preparation.error || { code: "DISCOVERY_PREPARATION_FAILED", message: "Preparation failed." },
          created.run_id
        );
        return;
      }
      setFlowState("EXECUTING_DISCOVERY");
      const execution = await executeDiscoveryRun(
        created.run_id,
        { resume: resumeExisting, force_restart: forceRestartExecution, target_horizon: runHorizon },
        controller.signal
      );
      if (execution.error) {
        await handleApiError(execution.error, created.run_id, true);
        return;
      }
      await loadResult(created.run_id, controller.signal);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      if (caught instanceof DiscoveryApiException) {
        await handleApiError(caught.apiError, activeRunId);
      } else {
        await handleApiError({ code: "DISCOVERY_REQUEST_FAILED", message: "Discovery request failed." });
      }
    } finally {
      inFlightRef.current = false;
    }
  };

  const loadExisting = async () => {
    if (inFlightRef.current || !existingRunIdValid || !existingRunId.trim()) return;
    inFlightRef.current = true;
    clearPolling();
    setError(null);
    try {
      await loadResult(existingRunId.trim());
    } catch (caught) {
      if (caught instanceof DiscoveryApiException) {
        await handleApiError(caught.apiError);
      }
    } finally {
      inFlightRef.current = false;
    }
  };

  const resumePreparation = async () => {
    if (!activeRunId || inFlightRef.current) return;
    inFlightRef.current = true;
    setFlowState("PREPARING_DATA");
    setError(null);
    try {
      const preparation = await prepareDiscoveryRun(activeRunId, {
        resume: resumeExisting,
        force_restart: forceRestartPreparation,
      });
      setPreparationStages(preparation.stage_results);
      setFlowState(statusFromResult(preparation.status));
      setError(preparation.error || null);
    } catch (caught) {
      if (caught instanceof DiscoveryApiException) await handleApiError(caught.apiError, activeRunId);
    } finally {
      inFlightRef.current = false;
    }
  };

  const resumeExecution = async () => {
    if (!activeRunId || inFlightRef.current) return;
    inFlightRef.current = true;
    setFlowState("EXECUTING_DISCOVERY");
    setError(null);
    try {
      const execution = await executeDiscoveryRun(activeRunId, {
        resume: resumeExisting,
        force_restart: forceRestartExecution,
        target_horizon: runHorizon,
      });
      setError(execution.error || null);
      await loadResult(activeRunId);
    } catch (caught) {
      if (caught instanceof DiscoveryApiException) await handleApiError(caught.apiError, activeRunId, true);
    } finally {
      inFlightRef.current = false;
    }
  };

  useEffect(() => {
    clearPolling();
    if (!activeRunId || result?.status !== "RUNNING") return;
    setIsPolling(true);
    const poll = async () => {
      if (pollingRequestRef.current) return;
      pollingRequestRef.current = true;
      try {
        const loaded = await getDiscoveryResult(activeRunId);
        setResult(loaded);
        if (loaded.status === "RUNNING") {
          pollRef.current = setTimeout(poll, 5000);
        } else {
          setFlowState(statusFromResult(loaded.status));
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
      <header className="page-header">
        <div>
          <p className="eyebrow">Financial analytics</p>
          <h1>Sector Discovery</h1>
        </div>
        <span className={`state-pill state-${flowState.toLowerCase()}`} aria-live="polite">
          {flowState.replace(/_/g, " ")}
        </span>
      </header>

      <section className="dashboard-grid">
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

        <section className="panel progress-panel" aria-label="Pipeline progress">
          <div className="panel-title">
            <h2>Pipeline Progress</h2>
            {isPolling && <span className="spinner-label">Refreshing result...</span>}
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

      <section className="panel results-panel">
        <div className="panel-title">
          <h2>Horizon Results</h2>
          <div className="tabs" role="tablist" aria-label="Discovery horizons">
            {HORIZONS.map(({ key, label }) => (
              <button
                key={key}
                role="tab"
                type="button"
                aria-selected={activeHorizon === key}
                className={activeHorizon === key ? "tab active" : "tab"}
                onClick={() => setActiveHorizon(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="tabs" role="tablist" aria-label="Result Views" style={{ marginTop: '1rem', marginBottom: '1rem' }}>
          <button role="tab" type="button" aria-selected={activeViewTab === "SECTORS"} className={activeViewTab === "SECTORS" ? "tab active" : "tab"} onClick={() => setActiveViewTab("SECTORS")}>Sectors</button>
          <button role="tab" type="button" aria-selected={activeViewTab === "INDUSTRIES"} className={activeViewTab === "INDUSTRIES" ? "tab active" : "tab"} onClick={() => setActiveViewTab("INDUSTRIES")}>Industries</button>
          <button role="tab" type="button" aria-selected={activeViewTab === "BASIC_INDUSTRIES"} className={activeViewTab === "BASIC_INDUSTRIES" ? "tab active" : "tab"} onClick={() => setActiveViewTab("BASIC_INDUSTRIES")}>Basic Industries</button>
          <button role="tab" type="button" aria-selected={activeViewTab === "STOCKS"} className={activeViewTab === "STOCKS" ? "tab active" : "tab"} onClick={() => setActiveViewTab("STOCKS")}>Stocks</button>
        </div>

        {emptyMessage ? (
          <div className="empty-state">{emptyMessage}</div>
        ) : (
          <div className="result-content">
            {activeViewTab === "SECTORS" && <GroupTable title="Sectors" groups={selectedHorizonView.sectors} onRowClick={(name, ps, pi) => setSelectedGroup({type: 'SECTOR', name, parentSector: ps, parentIndustry: pi})} />}
            {activeViewTab === "INDUSTRIES" && <GroupTable title="Industries" groups={selectedHorizonView.industries} showParentSector={true} onRowClick={(name, ps, pi) => setSelectedGroup({type: 'INDUSTRY', name, parentSector: ps, parentIndustry: pi})} />}
            {activeViewTab === "BASIC_INDUSTRIES" && <GroupTable title="Basic Industries" groups={selectedHorizonView.basicIndustries} showParentSector={true} showParentIndustry={true} onRowClick={(name, ps, pi) => setSelectedGroup({type: 'BASIC_INDUSTRY', name, parentSector: ps, parentIndustry: pi})} />}
            {activeViewTab === "STOCKS" && <StocksTable stocks={selectedHorizonView.stocks} />}
          </div>
        )}
      </section>

      {selectedGroup && activeRunId && (
        <ConstituentsModal
          runId={activeRunId}
          horizon={activeHorizon}
          entityType={selectedGroup.type}
          entityName={selectedGroup.name}
          parentSector={selectedGroup.parentSector}
          parentIndustry={selectedGroup.parentIndustry}
          onClose={() => setSelectedGroup(null)}
        />
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
              className={onRowClick ? "clickable-row" : ""}
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

function StocksTable({ stocks }: { stocks: DiscoveryStockResult[] }) {
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
            <th>Macro Score</th>
            <th>Coverage</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((stock) => (
            <tr key={`${stock.rank}-${stock.symbol}`}>
              <td>{stock.rank || "-"}</td>
              <td>{stock.symbol}</td>
              <td>{scoreText(stock.final_score)}</td>
              <td>{scoreText(stock.technical_score)}</td>
              <td>{scoreText(stock.fundamental_score)}</td>
              <td>{scoreText(stock.inherited_macro_score)}</td>
              <td>{finiteScore(stock.score_coverage_pct) ? `${stock.score_coverage_pct?.toFixed(1)}%` : "-"}</td>
              <td><span className={`badge ${stock.score_status?.toLowerCase()}`}>{stock.score_status || "-"}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConstituentsModal({
  runId,
  horizon,
  entityType,
  entityName,
  parentSector,
  parentIndustry,
  onClose
}: {
  runId: string;
  horizon: string;
  entityType: string;
  entityName: string;
  parentSector: string;
  parentIndustry: string;
  onClose: () => void;
}) {
  const [constituents, setConstituents] = useState<Constituent[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  const toggleRow = (symbol: string) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  };

  useEffect(() => {
    let active = true;
    const fetchConstituents = async () => {
      setLoading(true);
      try {
        const url = `/api/v1/discovery/runs/${runId}/constituents?horizon=${horizon}&entity_type=${entityType}&entity_name=${encodeURIComponent(entityName)}&parent_sector=${encodeURIComponent(parentSector)}&parent_industry=${encodeURIComponent(parentIndustry)}`;
        const res = await fetch(url);
        const data = await res.json();
        if (active && data.success) {
          setConstituents(data.data);
        }
      } catch (err) {
        console.error(err);
      } finally {
        if (active) setLoading(false);
      }
    };
    fetchConstituents();
    return () => { active = false; };
  }, [runId, horizon, entityType, entityName, parentSector, parentIndustry]);

  return (
    <div className="modal-overlay" onClick={onClose} style={{ position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', background: 'rgba(0,0,0,0.5)', display: 'flex', justifyContent: 'center', alignItems: 'center', zIndex: 1000 }}>
      <div className="modal-content panel" onClick={(e) => e.stopPropagation()} style={{ background: '#1c1c1e', padding: '20px', borderRadius: '8px', width: '90vw', maxWidth: '1000px', maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', border: '1px solid #3a3a3c' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h2>{entityName} Constituents</h2>
          <button onClick={onClose} className="secondary" style={{ padding: '4px 8px' }}>Close</button>
        </div>
        
        {loading ? (
          <div className="empty-state">Loading constituents...</div>
        ) : constituents.length === 0 ? (
          <div className="empty-state">No constituents found.</div>
        ) : (
          <div className="table-wrap" style={{ overflowY: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: '40px' }}></th>
                  <th>Symbol</th>
                  <th>Sector</th>
                  <th>Industry</th>
                  <th>Technical Score</th>
                  <th>Fundamental Score</th>
                </tr>
              </thead>
              <tbody>
                {constituents.map((c) => (
                  <Fragment key={c.symbol}>
                    <tr onClick={() => toggleRow(c.symbol)} style={{ cursor: 'pointer' }} className="clickable-row">
                      <td style={{ textAlign: 'center' }}>{expandedRows.has(c.symbol) ? '−' : '+'}</td>
                      <td>{c.symbol}</td>
                      <td>{c.sector}</td>
                      <td>{c.industry}</td>
                      <td>{c.technical_score !== null ? c.technical_score.toFixed(1) : "-"}</td>
                      <td>{c.fundamental_score !== null ? c.fundamental_score.toFixed(1) : "-"}</td>
                    </tr>
                    {expandedRows.has(c.symbol) && (
                      <tr>
                        <td colSpan={6} style={{ padding: '0' }}>
                          <ConstituentDetails constituent={c} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function ConstituentDetails({ constituent: c }: { constituent: Constituent }) {
  const tech = c.tech_details;
  const fund = c.fund_details;

  return (
    <div style={{ padding: '16px', background: '#252528', display: 'flex', gap: '24px', flexWrap: 'wrap', borderBottom: '1px solid #3a3a3c' }}>
      <div style={{ flex: '1 1 300px' }}>
        <h4 style={{ margin: '0 0 12px 0', color: '#4da6ff' }}>Technical Details</h4>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '0.9rem' }}>
          <span style={{ color: '#888' }}>Company Return:</span>
          <span style={{ fontWeight: 600, color: (c.company_return || 0) >= 0 ? '#4caf50' : '#f44336' }}>
            {c.company_return != null ? c.company_return.toFixed(2) + '%' : '-'}
          </span>
          
          <span style={{ color: '#888' }}>Benchmark Return:</span>
          <span>{c.benchmark_return != null ? c.benchmark_return.toFixed(2) + '%' : '-'}</span>
          
          <span style={{ color: '#888' }}>Volume Change:</span>
          <span>{tech?.technical_score?.components?.volume?.score?.toFixed(1) || '-'} pts</span>

          <span style={{ color: '#888' }}>Consistency Score:</span>
          <span>{tech?.technical_score?.components?.consistency?.score?.toFixed(1) || '-'} pts</span>
          
          <span style={{ color: '#888' }}>Pos. Periods / Valid:</span>
          <span>
            {tech?.consistency?.positive_periods ?? '-'}/{tech?.consistency?.valid_periods ?? '-'}
          </span>

          <span style={{ color: '#888' }}>Outperformed Periods:</span>
          <span>
            {tech?.consistency?.outperforming_periods ?? '-'}/{tech?.consistency?.valid_periods ?? '-'}
          </span>
        </div>
      </div>
      
      <div style={{ flex: '1 1 400px' }}>
        <h4 style={{ margin: '0 0 12px 0', color: '#ffd54f' }}>Fundamental Details</h4>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '0.9rem' }}>
          <span style={{ color: '#888' }}>Sales Growth:</span>
          <span style={{ fontWeight: 600, color: (fund?.fundamental_scoring?.growth?.sales_growth?.company_value || 0) >= 0 ? '#4caf50' : '#f44336' }}>
            {fund?.fundamental_scoring?.growth?.sales_growth?.company_value != null ? fund.fundamental_scoring.growth.sales_growth.company_value.toFixed(2) + '%' : '-'}
          </span>
          
          <span style={{ color: '#888' }}>Operating Margin:</span>
          <span style={{ fontWeight: 600 }}>
            {fund?.fundamental_scoring?.profitability?.operating_margin?.company_value != null ? fund.fundamental_scoring.profitability.operating_margin.company_value.toFixed(2) + '%' : '-'}
          </span>
          
          <span style={{ color: '#888' }}>Margin Trend (Change):</span>
          <span>
            {fund?.fundamental_scoring?.profitability?.margin_trend?.company_value != null ? fund.fundamental_scoring.profitability.margin_trend.company_value.toFixed(2) + ' pp' : '-'}
          </span>
          
          <span style={{ color: '#888' }}>Debt to Equity:</span>
          <span>
            {fund?.fundamental_scoring?.financial_strength?.debt_to_equity?.company_value != null ? fund.fundamental_scoring.financial_strength.debt_to_equity.company_value.toFixed(2) : '-'}
          </span>
          
          <span style={{ color: '#888' }}>Borrowing Change:</span>
          <span>
            {fund?.fundamental_scoring?.financial_strength?.borrowing_trend?.company_value != null ? fund.fundamental_scoring.financial_strength.borrowing_trend.company_value.toFixed(2) + '%' : '-'}
          </span>

          <span style={{ color: '#888' }}>OCF to PAT:</span>
          <span>
            {fund?.fundamental_scoring?.earnings_quality?.latest_cash_conversion?.company_value != null ? fund.fundamental_scoring.earnings_quality.latest_cash_conversion.company_value.toFixed(2) : '-'}
          </span>
          
          <span style={{ color: '#888' }}>Positive PAT Ratio:</span>
          <span>
            {fund?.fundamental_scoring?.earnings_quality?.profit_history?.positive_pat_period_ratio != null ? fund.fundamental_scoring.earnings_quality.profit_history.positive_pat_period_ratio.toFixed(1) + '%' : '-'}
          </span>
        </div>
      </div>
    </div>
  );
}
