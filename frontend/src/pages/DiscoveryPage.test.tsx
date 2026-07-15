import { fireEvent, render, screen, waitFor, within, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DiscoveryPage } from "./DiscoveryPage";

type FetchCall = {
  url: string;
  init?: RequestInit;
};

const createPayload = {
  success: true,
  data: {
    run_id: "run-test",
    as_of_date: "2026-07-14",
    status: "PENDING",
    created_at: "2026-07-14T10:00:00Z",
  },
};

const preparePayload = {
  success: true,
  data: {
    run_id: "run-test",
    status: "COMPLETED",
    preparation_status: "COMPLETED",
    last_completed_stage: "UPSTREAM_VALIDATION",
    resume_count: 0,
    stage_results: {
      UNIVERSE_SNAPSHOT: { status: "COMPLETED", warnings: [] },
      COMPANY_TECHNICAL: { status: "COMPLETED", warnings: [] },
      COMPANY_FUNDAMENTAL: { status: "COMPLETED", warnings: [] },
    },
    warnings: [],
    error: null,
  },
};

const executePayload = {
  success: true,
  data: {
    run_id: "run-test",
    status: "COMPLETED",
    last_completed_stage: "STOCK_SELECTION",
    resume_count: 0,
    horizons: {},
    stage_results: {
      STOCK_SELECTION: { status: "COMPLETED", warnings: [] },
    },
    warnings: [],
    error: null,
  },
};

function resultPayload(status = "COMPLETED", overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    data: {
      run_id: "run-test",
      status,
      current_stage: status === "RUNNING" ? "STOCK_SELECTION" : null,
      last_completed_stage: status === "RUNNING" ? "BASIC_INDUSTRY_SELECTION" : "STOCK_SELECTION",
      started_at: "2026-07-14T10:00:00Z",
      completed_at: status === "RUNNING" ? null : "2026-07-14T10:12:00Z",
      resume_count: 0,
      warnings: [],
      error: null,
      stage_results: {
        MACRO_SEARCH: { status: "COMPLETED", warnings: [] },
        MACRO_FILTER: { status: "COMPLETED", warnings: [] },
        SECTOR_SELECTION: { status: "COMPLETED", warnings: [], horizons: { SHORT: { status: "COMPLETED", warnings: [] } } },
        INDUSTRY_SELECTION: { status: "COMPLETED", warnings: [], horizons: { SHORT: { status: "COMPLETED", warnings: [] } } },
        BASIC_INDUSTRY_SELECTION: { status: "COMPLETED", warnings: [], horizons: { SHORT: { status: "COMPLETED", warnings: [] } } },
        STOCK_SELECTION: { status: status === "RUNNING" ? "RUNNING" : "COMPLETED", warnings: [] },
      },
      horizons: {
        SHORT: {
          status: "COMPLETED",
          sector: group("Technology", 1),
          industry: { ...group("Software", 1), parent_sector: "Technology" },
          basic_industry: {
            ...group("Enterprise Software", 1),
            parent_sector: "Technology",
            parent_industry: "Software",
          },
          stocks: [
            stock("BBB", 2, 80),
            stock("AAA", 1, 84),
          ],
          warnings: [],
        },
        MID: {
          status: "PENDING",
          sector: null,
          industry: null,
          basic_industry: null,
          stocks: [],
          warnings: [],
        },
        LONG: {
          status: "PENDING",
          sector: null,
          industry: null,
          basic_industry: null,
          stocks: [],
          warnings: [],
        },
      },
      ...overrides,
    },
  };
}

function group(name: string, rank: number) {
  return {
    name,
    rank,
    constituent_count: 42,
    final_score: 82,
    technical_score: 81,
    fundamental_score: 80,
    macro_score: 79,
    coverage_pct: 95,
    status: "STRONG",
    warnings: [],
  };
}

function stock(symbol: string, rank: number, score: number) {
  return {
    company_id: symbol.toLowerCase(),
    symbol,
    rank,
    selected: true,
    final_score: score,
    technical_score: score - 1,
    fundamental_score: score - 2,
    inherited_macro_score: 79,
    score_coverage_pct: 93,
    score_status: "STRONG",
    warnings: [],
  };
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );
}

function mockFetchSequence(bodies: Array<{ body: unknown; status?: number }>) {
  const calls: FetchCall[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: input.toString(), init });
    const next = bodies.shift();
    if (!next) throw new Error("Unexpected fetch call");
    return jsonResponse(next.body, next.status || 200);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

async function runHappyFlow() {
  const fetchState = mockFetchSequence([
    { body: createPayload },
    { body: preparePayload },
    { body: executePayload },
    { body: resultPayload() },
  ]);
  render(<DiscoveryPage />);
  await userEvent.click(screen.getByRole("button", { name: /start discovery/i }));
  await screen.findAllByText("Technology");
  return fetchState;
}

describe("DiscoveryPage", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("renders the page", () => {
    render(<DiscoveryPage />);
    expect(screen.getByRole("heading", { name: "Sector Discovery" })).toBeInTheDocument();
  });

  it("renders SHORT, MID, and LONG tabs", () => {
    render(<DiscoveryPage />);
    expect(screen.getByRole("tab", { name: "Short Term" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Mid Term" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Long Term" })).toBeInTheDocument();
  });

  it("renders simplified progress stages", async () => {
    await runHappyFlow();
    [
      "UNIVERSE_SNAPSHOT",
      "COMPANY_TECHNICAL",
      "COMPANY_FUNDAMENTAL",
      "MACRO_SEARCH",
      "MACRO_FILTER",
      "SECTOR_SELECTION",
      "INDUSTRY_SELECTION",
      "BASIC_INDUSTRY_SELECTION",
      "STOCK_SELECTION",
      "COMPLETED",
    ].forEach((stage) => {
      expect(screen.getAllByText(stage).length).toBeGreaterThan(0);
    });
  });

  it("does not render removed global hierarchy stages", async () => {
    await runHappyFlow();
    [
      "TECHNICAL_SECTOR",
      "TECHNICAL_INDUSTRY",
      "TECHNICAL_BASIC_INDUSTRY",
      "FUNDAMENTAL_SECTOR",
      "FUNDAMENTAL_INDUSTRY",
      "FUNDAMENTAL_BASIC_INDUSTRY",
      "SECTOR_IMPACT",
      "SECTOR_MACRO_SCORE",
      "INDUSTRY_IMPACT",
      "INDUSTRY_MACRO_SCORE",
      "BASIC_INDUSTRY_IMPACT",
      "BASIC_INDUSTRY_MACRO_SCORE",
      "STOCK_CANDIDATE_UNIVERSE",
      "STOCK_CANDIDATE_SCORE",
      "STOCK_RANKING",
    ].forEach((stage) => {
      expect(screen.queryByText(stage)).not.toBeInTheDocument();
    });
  });

  it("calls create run API", async () => {
    const { calls } = await runHappyFlow();
    expect(calls[0].url).toBe("/api/v1/discovery/runs");
  });

  it("calls prepare after create", async () => {
    const { calls } = await runHappyFlow();
    expect(calls[1].url).toBe("/api/v1/discovery/runs/run-test/prepare");
  });

  it("calls execute after successful prepare", async () => {
    const { calls } = await runHappyFlow();
    expect(calls[2].url).toBe("/api/v1/discovery/runs/run-test/execute");
  });

  it("fetches result after execution", async () => {
    const { calls } = await runHappyFlow();
    expect(calls[3].url).toBe("/api/v1/discovery/runs/run-test/result");
  });

  it("stops execution after prepare failure", async () => {
    const failedPrepare = {
      success: true,
      data: {
        ...preparePayload.data,
        status: "FAILED",
        error: { code: "BENCHMARK_DATA_UNAVAILABLE", message: "benchmark missing" },
      },
    };
    const { calls } = mockFetchSequence([
      { body: createPayload },
      { body: failedPrepare },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByRole("button", { name: /start discovery/i }));
    expect((await screen.findAllByText(/NIFTY 500 benchmark data is unavailable/i)).length).toBeGreaterThan(0);
    expect(calls).toHaveLength(2);
  });

  it("loads persisted result when execution returns a failed result", async () => {
    const failedExecute = {
      success: true,
      data: {
        ...executePayload.data,
        status: "FAILED",
        error: { code: "SAFE_STAGE_FAILURE", message: "safe failure" },
      },
    };
    const { calls } = mockFetchSequence([
      { body: createPayload },
      { body: preparePayload },
      { body: failedExecute },
      { body: resultPayload("FAILED", { error: { code: "SAFE_STAGE_FAILURE", message: "safe failure" } }) },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByRole("button", { name: /start discovery/i }));
    await screen.findAllByText("Technology");
    expect(calls[3].url).toBe("/api/v1/discovery/runs/run-test/result");
  });

  it("prevents duplicate clicks", async () => {
    const { fetchMock } = mockFetchSequence([
      { body: createPayload },
      { body: preparePayload },
      { body: executePayload },
      { body: resultPayload() },
    ]);
    render(<DiscoveryPage />);
    const button = screen.getByRole("button", { name: /start discovery/i });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findAllByText("Technology");
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("loads an existing run without executing", async () => {
    const { calls } = mockFetchSequence([{ body: resultPayload() }]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByText("Advanced Options"));
    await userEvent.type(screen.getByLabelText("Load Existing Run"), "run-test");
    await userEvent.click(screen.getByRole("button", { name: "Load Result" }));
    await screen.findAllByText("Technology");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/v1/discovery/runs/run-test/result");
  });

  it("starts polling for running results", async () => {
    vi.useFakeTimers();
    mockFetchSequence([
      { body: resultPayload("RUNNING") },
      { body: resultPayload("COMPLETED") },
    ]);
    render(<DiscoveryPage />);
    fireEvent.click(screen.getByText("Advanced Options"));
    fireEvent.change(screen.getByLabelText("Load Existing Run"), { target: { value: "run-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Load Result" }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("Refreshing result...")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(5000);
      await Promise.resolve();
    });
    expect(screen.getAllByText("COMPLETED").length).toBeGreaterThan(0);
  });

  it("stops polling after completion", async () => {
    vi.useFakeTimers();
    const { fetchMock } = mockFetchSequence([
      { body: resultPayload("RUNNING") },
      { body: resultPayload("COMPLETED") },
    ]);
    render(<DiscoveryPage />);
    fireEvent.click(screen.getByText("Advanced Options"));
    fireEvent.change(screen.getByLabelText("Load Existing Run"), { target: { value: "run-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Load Result" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("Refreshing result...")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(5000);
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => {
      vi.advanceTimersByTime(10000);
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops polling on unmount", async () => {
    vi.useFakeTimers();
    const { fetchMock } = mockFetchSequence([{ body: resultPayload("RUNNING") }]);
    const rendered = render(<DiscoveryPage />);
    fireEvent.click(screen.getByText("Advanced Options"));
    fireEvent.change(screen.getByLabelText("Load Existing Run"), { target: { value: "run-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Load Result" }));
    rendered.unmount();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders sector result", async () => {
    await runHappyFlow();
    expect(screen.getByRole("heading", { name: "Sector" })).toBeInTheDocument();
    expect(screen.getAllByText("Technology").length).toBeGreaterThan(0);
  });

  it("renders industry result", async () => {
    await runHappyFlow();
    expect(screen.getByRole("heading", { name: "Industry" })).toBeInTheDocument();
    expect(screen.getAllByText("Software").length).toBeGreaterThan(0);
  });

  it("renders basic-industry result", async () => {
    await runHappyFlow();
    expect(screen.getByRole("heading", { name: "Basic Industry" })).toBeInTheDocument();
    expect(screen.getAllByText("Enterprise Software").length).toBeGreaterThan(0);
  });

  it("renders different selected paths per horizon", async () => {
    await runHappyFlow();
    await userEvent.click(screen.getByRole("tab", { name: "Long Term" }));
    expect(screen.getByText("Sector selection has not completed for this horizon.")).toBeInTheDocument();

    cleanup();
    mockFetchSequence([
      {
        body: resultPayload("COMPLETED", {
          horizons: {
            SHORT: {
              status: "COMPLETED",
              sector: group("Technology", 1),
              industry: { ...group("Software", 1), parent_sector: "Technology" },
              basic_industry: { ...group("Enterprise Software", 1), parent_sector: "Technology", parent_industry: "Software" },
              stocks: [stock("AAA", 1, 84)],
              warnings: [],
            },
            MID: {
              status: "COMPLETED",
              sector: group("Financials", 1),
              industry: { ...group("Banks", 1), parent_sector: "Financials" },
              basic_industry: { ...group("Private Banks", 1), parent_sector: "Financials", parent_industry: "Banks" },
              stocks: [stock("FIN", 1, 76)],
              warnings: [],
            },
            LONG: {
              status: "COMPLETED",
              sector: group("Industrials", 1),
              industry: { ...group("Capital Goods", 1), parent_sector: "Industrials" },
              basic_industry: { ...group("Electrical Equipment", 1), parent_sector: "Industrials", parent_industry: "Capital Goods" },
              stocks: [stock("IND", 1, 71)],
              warnings: [],
            },
          },
        }),
      },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByText("Advanced Options"));
    await userEvent.type(screen.getByLabelText("Load Existing Run"), "run-test");
    await userEvent.click(screen.getByRole("button", { name: "Load Result" }));
    await screen.findAllByText("Technology");
    await userEvent.click(screen.getByRole("tab", { name: "Mid Term" }));
    expect(screen.getAllByText("Financials").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Banks").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("tab", { name: "Long Term" }));
    expect(screen.getAllByText("Industrials").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Capital Goods").length).toBeGreaterThan(0);
  });

  it("renders hierarchy mismatch warning instead of invalid industry", async () => {
    mockFetchSequence([
      {
        body: resultPayload("COMPLETED", {
          horizons: {
            SHORT: {
              status: "COMPLETED",
              sector: group("Technology", 1),
              industry: { ...group("Banks", 1), parent_sector: "Financials" },
              basic_industry: null,
              stocks: [],
              warnings: ["SELECTION_HIERARCHY_MISMATCH"],
            },
            MID: { status: "PENDING", sector: null, industry: null, basic_industry: null, stocks: [], warnings: [] },
            LONG: { status: "PENDING", sector: null, industry: null, basic_industry: null, stocks: [], warnings: [] },
          },
        }),
      },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByText("Advanced Options"));
    await userEvent.type(screen.getByLabelText("Load Existing Run"), "run-test");
    await userEvent.click(screen.getByRole("button", { name: "Load Result" }));
    expect(await screen.findByText("No eligible Industry was found inside the selected Sector.")).toBeInTheDocument();
    expect(screen.getAllByText("SELECTION_HIERARCHY_MISMATCH").length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "Industry" })).not.toBeInTheDocument();
  });

  it("displays stocks in persisted rank order", async () => {
    await runHappyFlow();
    const rows = within(screen.getByRole("table", { name: "Selected Stocks" })).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("2BBB");
    expect(rows[2]).toHaveTextContent("1AAA");
  });

  it("renders persisted stock technical and inherited macro scores", async () => {
    await runHappyFlow();
    const table = screen.getByRole("table", { name: "Selected Stocks" });
    expect(within(table).getByText("Technical Score")).toBeInTheDocument();
    expect(within(table).getByText("Inherited Basic Industry Macro Score")).toBeInTheDocument();
    expect(within(table).getAllByText("79.0").length).toBeGreaterThan(0);
  });

  it("renders empty horizon state", async () => {
    await runHappyFlow();
    await userEvent.click(screen.getByRole("tab", { name: "Mid Term" }));
    expect(screen.getByText("Sector selection has not completed for this horizon.")).toBeInTheDocument();
  });

  it("renders empty sector, industry, basic industry, and stock states", async () => {
    const baseHorizons = {
      MID: { status: "PENDING", sector: null, industry: null, basic_industry: null, stocks: [], warnings: [] },
      LONG: { status: "PENDING", sector: null, industry: null, basic_industry: null, stocks: [], warnings: [] },
    };
    const cases = [
      {
        horizon: { status: "COMPLETED", sector: null, industry: null, basic_industry: null, stocks: [], warnings: [] },
        message: "No eligible Sector was found.",
      },
      {
        horizon: { status: "COMPLETED", sector: group("Technology", 1), industry: null, basic_industry: null, stocks: [], warnings: [] },
        message: "No eligible Industry was found inside the selected Sector.",
      },
      {
        horizon: {
          status: "COMPLETED",
          sector: group("Technology", 1),
          industry: { ...group("Software", 1), parent_sector: "Technology" },
          basic_industry: null,
          stocks: [],
          warnings: [],
        },
        message: "No eligible Basic Industry was found inside the selected Industry.",
      },
      {
        horizon: {
          status: "COMPLETED",
          sector: group("Technology", 1),
          industry: { ...group("Software", 1), parent_sector: "Technology" },
          basic_industry: { ...group("Enterprise Software", 1), parent_sector: "Technology", parent_industry: "Software" },
          stocks: [],
          warnings: [],
        },
        message: "No eligible stocks were found inside the selected Basic Industry.",
      },
    ];

    for (const item of cases) {
      cleanup();
      mockFetchSequence([{ body: resultPayload("COMPLETED", { horizons: { SHORT: item.horizon, ...baseHorizons } }) }]);
      render(<DiscoveryPage />);
      await userEvent.click(screen.getByText("Advanced Options"));
      await userEvent.type(screen.getByLabelText("Load Existing Run"), "run-test");
      await userEvent.click(screen.getByRole("button", { name: "Load Result" }));
      expect(await screen.findByText(item.message)).toBeInTheDocument();
    }
  });

  it("keeps successful horizons visible when another horizon failed", async () => {
    mockFetchSequence([
      {
        body: resultPayload("COMPLETED_WITH_WARNINGS", {
          horizons: {
            SHORT: resultPayload().data.horizons.SHORT,
            MID: { status: "FAILED", sector: null, industry: null, basic_industry: null, stocks: [], warnings: ["MID_FAILED"] },
            LONG: {
              status: "COMPLETED",
              sector: group("Industrials", 1),
              industry: { ...group("Capital Goods", 1), parent_sector: "Industrials" },
              basic_industry: { ...group("Electrical Equipment", 1), parent_sector: "Industrials", parent_industry: "Capital Goods" },
              stocks: [stock("IND", 1, 71)],
              warnings: [],
            },
          },
        }),
      },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByText("Advanced Options"));
    await userEvent.type(screen.getByLabelText("Load Existing Run"), "run-test");
    await userEvent.click(screen.getByRole("button", { name: "Load Result" }));
    expect((await screen.findAllByText("Technology")).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("tab", { name: "Mid Term" }));
    expect(screen.getByText("No eligible Sector was found.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Long Term" }));
    expect(screen.getAllByText("Industrials").length).toBeGreaterThan(0);
  });

  it("renders warnings", async () => {
    mockFetchSequence([
      { body: createPayload },
      { body: { ...preparePayload, data: { ...preparePayload.data, warnings: ["BENCHMARK_DATA_UNAVAILABLE"] } } },
      { body: executePayload },
      { body: resultPayload("COMPLETED", { warnings: ["BENCHMARK_DATA_UNAVAILABLE"] }) },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByRole("button", { name: /start discovery/i }));
    expect(await screen.findAllByText("BENCHMARK_DATA_UNAVAILABLE")).not.toHaveLength(0);
  });

  it("renders 404 error", async () => {
    mockFetchSequence([
      {
        status: 404,
        body: { success: false, error: { code: "DISCOVERY_RUN_NOT_FOUND", message: "missing" } },
      },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByText("Advanced Options"));
    await userEvent.type(screen.getByLabelText("Load Existing Run"), "missing");
    await userEvent.click(screen.getByRole("button", { name: "Load Result" }));
    expect((await screen.findAllByText("Discovery run was not found.")).length).toBeGreaterThan(0);
  });

  it("renders 409 error", async () => {
    mockFetchSequence([
      { body: createPayload },
      {
        status: 409,
        body: { success: false, error: { code: "DISCOVERY_RUN_ALREADY_RUNNING", message: "busy" } },
      },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByRole("button", { name: /start discovery/i }));
    expect((await screen.findAllByText("This discovery run is already being processed.")).length).toBeGreaterThan(0);
  });

  it("renders missing NIFTY 500 message", async () => {
    mockFetchSequence([
      { body: createPayload },
      {
        status: 422,
        body: { success: false, error: { code: "BENCHMARK_DATA_UNAVAILABLE", message: "benchmark" } },
      },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByRole("button", { name: /start discovery/i }));
    expect((await screen.findAllByText(/NIFTY 500 benchmark data is unavailable/i)).length).toBeGreaterThan(0);
  });

  it("renders missing Parallel API key message", async () => {
    mockFetchSequence([
      { body: createPayload },
      { body: preparePayload },
      {
        status: 500,
        body: {
          success: false,
          error: { code: "DISCOVERY_PIPELINE_EXECUTION_FAILED", message: "PARALLEL_API_KEY missing" },
        },
      },
    ]);
    render(<DiscoveryPage />);
    await userEvent.click(screen.getByRole("button", { name: /start discovery/i }));
    expect((await screen.findAllByText(/Parallel.ai is not configured/i)).length).toBeGreaterThan(0);
  });

  it("does not render trading recommendation fields", async () => {
    await runHappyFlow();
    expect(screen.queryByText(/BUY|SELL|entry|target|stop loss|quantity|expected return/i)).not.toBeInTheDocument();
  });

  it("keeps mobile stock table contained", async () => {
    await runHappyFlow();
    window.innerWidth = 390;
    fireEvent(window, new Event("resize"));
    const tableWrap = screen.getByRole("table", { name: "Selected Stocks" }).parentElement;
    expect(tableWrap).toHaveClass("table-wrap");
  });
});
