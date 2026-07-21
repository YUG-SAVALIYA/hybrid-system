import { 
  createDiscoveryRun, 
  prepareDiscoveryRun, 
  executeDiscoveryRun, 
  getDiscoveryResult,
  DiscoveryApiError,
  DiscoveryResult,
  DiscoveryHorizon,
  DiscoveryPreparationResult,
  DiscoveryStageResult
} from "../api/discovery";

export type FlowState = "IDLE" | "CREATING_RUN" | "PREPARING_DATA" | "EXECUTING_DISCOVERY" | "LOADING_RESULT" | "COMPLETED" | "COMPLETED_WITH_WARNINGS" | "FAILED" | "RUNNING";

export interface RunState {
  activeRunId: string | null;
  flowState: FlowState;
  error: DiscoveryApiError | null;
  preparationStages: Record<string, DiscoveryStageResult>;
  result: DiscoveryResult | null;
}

type Listener = (state: RunState) => void;

class RunManager {
  private state: RunState = {
    activeRunId: null,
    flowState: "IDLE",
    error: null,
    preparationStages: {},
    result: null,
  };
  private listeners: Set<Listener> = new Set();
  private abortController: AbortController | null = null;
  private pollInterval: number | null = null;

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    listener(this.state);
    return () => this.listeners.delete(listener);
  }

  private notify() {
    this.listeners.forEach(l => l(this.state));
  }

  getState() {
    return this.state;
  }

  abort() {
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
    this.clearPolling();
  }

  reset() {
    this.abort();
    this.state = {
      activeRunId: null,
      flowState: "IDLE",
      error: null,
      preparationStages: {},
      result: null,
    };
    this.notify();
  }

  private clearPolling() {
    if (this.pollInterval !== null) {
      window.clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  async startRun(
    customRunId: string, 
    runHorizon: DiscoveryHorizon, 
    resumeExisting: boolean, 
    forceRestartPreparation: boolean, 
    forceRestartExecution: boolean,
    onRunCreated?: (runId: string) => void
  ) {
    if (["CREATING_RUN", "PREPARING_DATA", "EXECUTING_DISCOVERY", "LOADING_RESULT"].includes(this.state.flowState)) {
      return; 
    }
    
    this.reset();
    this.abortController = new AbortController();
    const signal = this.abortController.signal;

    try {
      this.state.flowState = "CREATING_RUN";
      this.notify();

      const created = await createDiscoveryRun(
        { run_id: customRunId.trim() || null, as_of_date: null },
        signal
      );
      this.state.activeRunId = created.run_id;
      if (onRunCreated) onRunCreated(created.run_id);
      
      this.state.flowState = "PREPARING_DATA";
      this.notify();

      const preparation = await prepareDiscoveryRun(
        created.run_id,
        { resume: resumeExisting, force_restart: forceRestartPreparation },
        signal
      );
      
      this.state.preparationStages = preparation.stage_results;
      this.notify();

      if (preparation.error || preparation.status === "FAILED") {
        this.state.error = preparation.error || { code: "DISCOVERY_PREPARATION_FAILED", message: "Preparation failed." };
        this.state.flowState = "FAILED";
        this.notify();
        return;
      }

      this.state.flowState = "EXECUTING_DISCOVERY";
      this.notify();

      const execution = await executeDiscoveryRun(
        created.run_id,
        { resume: resumeExisting, force_restart: forceRestartExecution, target_horizon: runHorizon },
        signal
      );

      if (execution.error) {
        this.state.error = execution.error;
        this.state.flowState = "FAILED";
        this.notify();
        return;
      }

      await this.loadResult(created.run_id, signal);

    } catch (caught: any) {
      if (caught.name === "AbortError") return;
      this.state.error = caught.apiError || { code: "DISCOVERY_REQUEST_FAILED", message: "Discovery request failed." };
      this.state.flowState = "FAILED";
      this.notify();
    }
  }

  async resumePreparation(forceRestart: boolean) {
    if (!this.state.activeRunId) return;
    this.abort();
    this.abortController = new AbortController();
    const signal = this.abortController.signal;
    this.state.error = null;
    this.state.flowState = "PREPARING_DATA";
    this.notify();

    try {
      const preparation = await prepareDiscoveryRun(
        this.state.activeRunId,
        { resume: true, force_restart: forceRestart },
        signal
      );
      this.state.preparationStages = preparation.stage_results;
      
      if (preparation.error || preparation.status === "FAILED") {
        this.state.error = preparation.error || { code: "DISCOVERY_PREPARATION_FAILED", message: "Preparation failed." };
        this.state.flowState = "FAILED";
        this.notify();
        return;
      }
      
      await this.loadResult(this.state.activeRunId, signal);

    } catch (caught: any) {
      if (caught.name === "AbortError") return;
      this.state.error = caught.apiError || { code: "DISCOVERY_REQUEST_FAILED", message: "Discovery request failed." };
      this.state.flowState = "FAILED";
      this.notify();
    }
  }

  async resumeExecution(runHorizon: DiscoveryHorizon, forceRestart: boolean) {
    if (!this.state.activeRunId) return;
    this.abort();
    this.abortController = new AbortController();
    const signal = this.abortController.signal;
    this.state.error = null;
    this.state.flowState = "EXECUTING_DISCOVERY";
    this.notify();

    try {
      const execution = await executeDiscoveryRun(
        this.state.activeRunId,
        { resume: true, force_restart: forceRestart, target_horizon: runHorizon },
        signal
      );

      if (execution.error) {
        this.state.error = execution.error;
        this.state.flowState = "FAILED";
        this.notify();
        return;
      }
      
      await this.loadResult(this.state.activeRunId, signal);

    } catch (caught: any) {
      if (caught.name === "AbortError") return;
      this.state.error = caught.apiError || { code: "DISCOVERY_REQUEST_FAILED", message: "Discovery request failed." };
      this.state.flowState = "FAILED";
      this.notify();
    }
  }

  async loadResult(runId: string, signal?: AbortSignal) {
    this.state.flowState = "LOADING_RESULT";
    this.state.activeRunId = runId;
    this.state.error = null;
    this.notify();
    try {
      const res = await getDiscoveryResult(runId, signal);
      this.state.result = res;
      this.state.preparationStages = res.stage_results || {};
      
      if (res.status === "COMPLETED_WITH_WARNINGS") this.state.flowState = "COMPLETED_WITH_WARNINGS";
      else if (res.status === "FAILED") this.state.flowState = "FAILED";
      else if (res.status === "RUNNING") {
        this.state.flowState = "RUNNING";
        this.startPolling();
      }
      else this.state.flowState = "COMPLETED";
      
      this.notify();
      return res;
    } catch (caught: any) {
      if (caught.name === "AbortError") return;
      this.state.error = caught.apiError || { code: "DISCOVERY_REQUEST_FAILED", message: "Discovery request failed." };
      this.state.flowState = "FAILED";
      this.notify();
      throw caught; 
    }
  }

  private startPolling() {
    this.clearPolling();
    this.pollInterval = window.setInterval(async () => {
      if (!this.state.activeRunId) {
        this.clearPolling();
        return;
      }
      try {
        const res = await getDiscoveryResult(this.state.activeRunId);
        this.state.result = res;
        this.state.preparationStages = res.stage_results || {};
        
        if (res.status !== "RUNNING") {
          this.clearPolling();
          if (res.status === "COMPLETED_WITH_WARNINGS") this.state.flowState = "COMPLETED_WITH_WARNINGS";
          else if (res.status === "FAILED") this.state.flowState = "FAILED";
          else this.state.flowState = "COMPLETED";
        }
        this.notify();
      } catch (e) {
        // Silently ignore polling errors
      }
    }, 5000);
  }
}

export const runManager = new RunManager();
