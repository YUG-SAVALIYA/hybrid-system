export type DiscoveryRunStatus =
  | "PENDING"
  | "RUNNING"
  | "FAILED"
  | "COMPLETED_WITH_WARNINGS"
  | "COMPLETED";

export type DiscoveryStageStatus =
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "COMPLETED_WITH_WARNINGS"
  | "FAILED"
  | "SKIPPED";

export type DiscoveryHorizon = "SHORT" | "MID" | "LONG";

export interface DiscoveryApiError {
  code: string;
  message: string;
}

export interface DiscoveryCreateRequest {
  run_id: string | null;
  as_of_date: string | null;
}

export interface DiscoveryPrepareRequest {
  resume: boolean;
  force_restart: boolean;
}

export interface DiscoveryExecuteRequest {
  resume: boolean;
  force_restart: boolean;
  target_horizon?: string | null;
}

export interface DiscoveryRunCreateData {
  run_id: string;
  as_of_date: string;
  status: "PENDING";
  created_at: string;
}

export interface DiscoveryGroupResult {
  name: string;
  rank?: number | null;
  selected?: boolean;
  constituent_count?: number | null;
  final_score?: number | null;
  technical_score?: number | null;
  fundamental_score?: number | null;
  macro_score?: number | null;
  status?: string | null;
  coverage_pct?: number | null;
  warnings?: string[];
  parent_sector?: string | null;
  parent_industry?: string | null;
  tech_details?: any;
  fund_details?: any;
  macro_details?: any;
}

export interface DiscoveryStockResult {
  company_id: string;
  symbol: string;
  rank?: number | null;
  selected: boolean;
  final_score?: number | null;
  technical_score?: number | null;
  fundamental_score?: number | null;
  inherited_macro_score?: number | null;
  score_status?: string | null;
  score_coverage_pct?: number | null;
  warnings?: string[];
}

export interface DiscoveryHorizonResult {
  status: DiscoveryStageStatus;
  sectors: DiscoveryGroupResult[];
  industries: DiscoveryGroupResult[];
  basic_industries: DiscoveryGroupResult[];
  stocks: DiscoveryStockResult[];
  warnings: string[];
}

export type DiscoveryStageResult = {
  status?: DiscoveryStageStatus;
  started_at?: string | null;
  completed_at?: string | null;
  warnings?: string[];
  error_code?: string | null;
  error_message?: string | null;
  metadata?: Record<string, string | number | boolean | null | string[] | number[] | Record<string, unknown>>;
  horizons?: Partial<Record<DiscoveryHorizon, DiscoveryStageResult>>;
};

export interface DiscoveryResult {
  run_id: string;
  status: DiscoveryRunStatus;
  current_stage?: string | null;
  last_completed_stage?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  resume_count: number;
  warnings: string[];
  error?: DiscoveryApiError | null;
  stage_results: Record<string, DiscoveryStageResult>;
  horizons: Record<DiscoveryHorizon, DiscoveryHorizonResult>;
  source_data_as_of?: string | null;
  as_of_date?: string | null;
}

export interface DiscoveryPreparationResult {
  run_id: string;
  status: DiscoveryRunStatus;
  preparation_status?: DiscoveryRunStatus;
  last_completed_stage?: string | null;
  resume_count: number;
  stage_results: Record<string, DiscoveryStageResult>;
  warnings: string[];
  error?: DiscoveryApiError | null;
}

type ApiSuccess<T> = { success: true; data: T };
type ApiFailure = { success: false; error: DiscoveryApiError };
type ApiResponse<T> = ApiSuccess<T> | ApiFailure;

export class DiscoveryApiException extends Error {
  readonly status: number;
  readonly apiError: DiscoveryApiError;

  constructor(status: number, apiError: DiscoveryApiError) {
    super(apiError.message);
    this.status = status;
    this.apiError = apiError;
  }
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) || "/api/v1";

function endpoint(path: string) {
  return `${API_BASE}${path}`;
}

async function request<T>(
  path: string,
  init: RequestInit,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(endpoint(path), {
    ...init,
    signal,
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
      ...(init.headers || {}),
    },
  });
  const body = (await response.json()) as ApiResponse<T>;
  if (!response.ok || body.success === false) {
    const error = body.success === false
      ? body.error
      : { code: "DISCOVERY_REQUEST_FAILED", message: "Discovery request failed." };
    throw new DiscoveryApiException(response.status, error);
  }
  return body.data;
}

export function createDiscoveryRun(
  payload: DiscoveryCreateRequest,
  signal?: AbortSignal
): Promise<DiscoveryRunCreateData> {
  return request<DiscoveryRunCreateData>(
    "/discovery/runs",
    { method: "POST", body: JSON.stringify(payload) },
    signal
  );
}

export function prepareDiscoveryRun(
  runId: string,
  payload: DiscoveryPrepareRequest,
  signal?: AbortSignal
): Promise<DiscoveryPreparationResult> {
  return request<DiscoveryPreparationResult>(
    `/discovery/runs/${encodeURIComponent(runId)}/prepare`,
    { method: "POST", body: JSON.stringify(payload) },
    signal
  );
}

export function executeDiscoveryRun(
  runId: string,
  payload: DiscoveryExecuteRequest,
  signal?: AbortSignal
): Promise<DiscoveryPreparationResult> {
  return request<DiscoveryPreparationResult>(
    `/discovery/runs/${encodeURIComponent(runId)}/execute`,
    { method: "POST", body: JSON.stringify(payload) },
    signal
  );
}

export function getDiscoveryResult(
  runId: string,
  signal?: AbortSignal
): Promise<DiscoveryResult> {
  return request<DiscoveryResult>(
    `/discovery/runs/${encodeURIComponent(runId)}/result`,
    { method: "GET" },
    signal
  );
}

export interface DiscoveryRunSummaryItem {
  name: string;
  rank?: number | null;
  final_score?: number | null;
}

export interface DiscoveryRunSummary {
  run_id: string;
  status: string;
  run_date?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  top_sectors: DiscoveryRunSummaryItem[];
  top_industries: DiscoveryRunSummaryItem[];
  top_basic_industries: DiscoveryRunSummaryItem[];
  top_stocks: DiscoveryRunSummaryItem[];
}

export function getRecentDiscoveryRuns(
  signal?: AbortSignal
): Promise<DiscoveryRunSummary[]> {
  return request<DiscoveryRunSummary[]>(
    "/discovery/runs",
    { method: "GET" },
    signal
  );
}
