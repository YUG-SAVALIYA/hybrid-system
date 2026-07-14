import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  { key: "TECHNICAL_COMPANY", label: "Technical Company Analysis", source: "preparation" },
  { key: "TECHNICAL_SECTOR", label: "Technical Sector Analysis", source: "preparation" },
  { key: "TECHNICAL_INDUSTRY", label: "Technical Industry Analysis", source: "preparation" },
  { key: "TECHNICAL_BASIC_INDUSTRY", label: "Technical Basic Industry Analysis", source: "preparation" },
  { key: "FUNDAMENTAL_COMPANY", label: "Fundamental Company Analysis", source: "preparation" },
  { key: "FUNDAMENTAL_SECTOR", label: "Fundamental Sector Analysis", source: "preparation" },
  { key: "FUNDAMENTAL_INDUSTRY", label: "Fundamental Industry Analysis", source: "preparation" },
  { key: "FUNDAMENTAL_BASIC_INDUSTRY", label: "Fundamental Basic Industry Analysis", source: "preparation" },
  { key: "MACRO_SEARCH", label: "Macro Search", source: "execution" },
  { key: "MACRO_FILTER", label: "Macro Filter", source: "execution" },
  { key: "SECTOR_IMPACT", label: "Sector Macro Impact", source: "execution" },
  { key: "SECTOR_RANKING", label: "Sector Ranking", source: "execution" },
  { key: "INDUSTRY_IMPACT", label: "Industry Macro Impact", source: "execution" },
  { key: "INDUSTRY_RANKING", label: "Industry Ranking", source: "execution" },
  { key: "BASIC_INDUSTRY_IMPACT", label: "Basic Industry Macro Impact", source: "execution" },
  { key: "BASIC_INDUSTRY_RANKING", label: "Basic Industry Ranking", source: "execution" },
  { key: "STOCK_CANDIDATE_UNIVERSE", label: "Stock Candidate Universe", source: "execution" },
  { key: "STOCK_CANDIDATE_SCORE", label: "Stock Candidate Scoring", source: "execution" },
  { key: "STOCK_RANKING", label: "Stock Ranking", source: "execution" },
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

function scoreText(value?: number | null) {
  return finiteScore(value) ? Number(value).toFixed(1) : "-";
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
    horizon?.warnings.forEach((code) => add(code, key));
    horizon?.sector?.warnings?.forEach((code) => add(code, `${key} Sector`));
    horizon?.industry?.warnings?.forEach((code) => add(code, `${key} Industry`));
    horizon?.basic_industry?.warnings?.forEach((code) => add(code, `${key} Basic Industry`));
    horizon?.stocks.forEach((stock) =>
      stock.warnings?.forEach((code) => add(code, `${key} ${stock.symbol}`))
    );
  });
  return items;
}

function groupEmptyMessage(horizon: DiscoveryHorizon, result: DiscoveryResult | null) {
  const data = result?.horizons[horizon];
  if (!data || data.status === "PENDING") return "This horizon has not been processed yet.";
  if (!data.sector) return "No eligible sector was found for this horizon.";
  if (!data.industry) return "No eligible industry was found.";
  if (!data.basic_industry) return "No eligible basic industry was found.";
  if (!data.stocks.length) return "No eligible stock candidates were found.";
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
      status,
      source: stage.source === "preparation" ? "Preparation" : "Discovery execution",
      timestamp: stageResult?.completed_at || stageResult?.started_at || null,
      detail: stageResult?.error_message || stageResult?.warnings?.join(", ") || null,
    });
    Object.entries(stageResult?.horizons || {}).forEach(([horizon, horizonResult]) => {
      items.push({
        id: `${stage.key}-${horizon}`,
        label: `${stage.label} - ${horizon}`,
        status: stageStatus(horizonResult),
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
  const [asOfDate, setAsOfDate] = useState(todayIso());
  const [customRunId, setCustomRunId] = useState("");
  const [existingRunId, setExistingRunId] = useState("");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [resumeExisting, setResumeExisting] = useState(true);
  const [forceRestartPreparation, setForceRestartPreparation] = useState(false);
  const [forceRestartExecution, setForceRestartExecution] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [activeHorizon, setActiveHorizon] = useState<DiscoveryHorizon>("SHORT");
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
  const selectedHorizon = result?.horizons[activeHorizon];
  const emptyMessage = groupEmptyMessage(activeHorizon, result);

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
        { run_id: customRunId.trim() || null, as_of_date: asOfDate || null },
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
        { resume: resumeExisting, force_restart: forceRestartExecution },
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
            As of Date
            <input
              type="date"
              value={asOfDate}
              max={todayIso()}
              onChange={(event) => setAsOfDate(event.target.value)}
            />
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
          <ol className="timeline">
            {STAGES.map((stage, index) => {
              const source = stage.source === "preparation" ? preparationStages : result?.stage_results || {};
              const stageResult = source[stage.key];
              const status = stageStatus(stageResult);
              return (
                <li className={`timeline-item stage-${status.toLowerCase()}`} key={stage.key}>
                  <span className="stage-index">{index + 1}</span>
                  <div>
                    <div className="stage-row">
                      <span>{stage.label}</span>
                      <span className="badge">{status}</span>
                    </div>
                    {status === "RUNNING" && <span className="inline-spinner">Running</span>}
                    {status === "FAILED" && stageResult?.error_message && (
                      <p className="stage-error">{stageResult.error_message}</p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      </section>

      <section className="panel process-log-panel" aria-label="Process log">
        <div className="panel-title">
          <h2>Process Log</h2>
          <span className="muted">{processLog.length} events</span>
        </div>
        {processLog.length ? (
          <ol className="process-log">
            {processLog.map((entry) => (
              <li key={entry.id}>
                <div>
                  <strong>{entry.label}</strong>
                  <span>{entry.source}</span>
                </div>
                <span className={`badge stage-${entry.status.toLowerCase()}`}>{entry.status}</span>
                <time>{entry.timestamp || "-"}</time>
                {entry.detail && <p>{entry.detail}</p>}
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">No process events yet.</p>
        )}
      </section>

      {safeError && (
        <section className={`alert ${/NIFTY 500|Parallel\.ai/.test(safeError) ? "blocking" : ""}`} role="alert">
          {safeError}
        </section>
      )}

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

        {emptyMessage ? (
          <div className="empty-state">{emptyMessage}</div>
        ) : (
          <div className="result-content">
            <div className="hierarchy-grid">
              <GroupCard title="Sector" group={selectedHorizon?.sector} />
              <GroupCard title="Industry" group={selectedHorizon?.industry} />
              <GroupCard title="Basic Industry" group={selectedHorizon?.basic_industry} />
            </div>
            <StocksTable stocks={selectedHorizon?.stocks || []} />
          </div>
        )}
      </section>

      <section className="side-grid">
        <details className="panel warnings-panel" open>
          <summary>Warnings and Errors</summary>
          {warnings.length ? (
            <ul className="warning-list">
              {warnings.map((warning) => (
                <li key={`${warning.code}-${warning.context}`}>
                  <strong>{warning.code}</strong>
                  <span>{warning.context}</span>
                  <p>{warning.message}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No warnings reported.</p>
          )}
        </details>

        <section className="panel metadata-panel">
          <h2>Run Metadata</h2>
          <dl>
            <Meta label="Run ID" value={activeRunId || result?.run_id} copyable />
            <Meta label="Run Status" value={result?.status || flowState} />
            <Meta label="As-of Date" value={result?.as_of_date || result?.source_data_as_of || asOfDate} />
            <Meta label="Current Stage" value={result?.current_stage} />
            <Meta label="Last Completed Stage" value={result?.last_completed_stage} />
            <Meta label="Started At" value={result?.started_at} />
            <Meta label="Completed At" value={result?.completed_at} />
            <Meta label="Resume Count" value={result?.resume_count?.toString() || "0"} />
          </dl>
        </section>
      </section>
    </main>
  );
}

function Meta({ label, value, copyable = false }: { label: string; value?: string | null; copyable?: boolean }) {
  return (
    <div className="meta-row">
      <dt>{label}</dt>
      <dd>
        <span>{value || "-"}</span>
        {copyable && value && (
          <button type="button" className="copy-button" onClick={() => void navigator.clipboard?.writeText(value)}>
            Copy
          </button>
        )}
      </dd>
    </div>
  );
}

function GroupCard({ title, group }: { title: string; group?: DiscoveryGroupResult | null }) {
  if (!group) return null;
  const score = finiteScore(group.final_score) ? Math.max(0, Math.min(100, group.final_score || 0)) : 0;
  return (
    <article className="result-card">
      <div className="card-heading">
        <h3>{title}</h3>
        <span className="badge">{group.status || "-"}</span>
      </div>
      <h4>{group.name}</h4>
      <div className="score-meter" aria-label={`${title} final score ${scoreText(group.final_score)}`}>
        <span style={{ width: `${score}%` }} />
      </div>
      <div className="metric-grid">
        <Metric label="Rank" value={group.rank?.toString()} />
        <Metric label="Final score" value={scoreText(group.final_score)} />
        <Metric label="Technical score" value={scoreText(group.technical_score)} />
        <Metric label="Fundamental score" value={scoreText(group.fundamental_score)} />
        <Metric label="Macro score" value={scoreText(group.macro_score)} />
        <Metric label="Coverage" value={finiteScore(group.coverage_pct) ? `${group.coverage_pct?.toFixed(1)}%` : "-"} />
        <Metric label="Parent sector" value={group.parent_sector || undefined} />
        <Metric label="Parent industry" value={group.parent_industry || undefined} />
      </div>
      {!!group.warnings?.length && <p className="card-warning">{group.warnings.join(", ")}</p>}
    </article>
  );
}

function Metric({ label, value }: { label: string; value?: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

function StocksTable({ stocks }: { stocks: DiscoveryStockResult[] }) {
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
              <td>{stock.score_status || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
