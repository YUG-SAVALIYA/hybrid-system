import { useEffect, useState } from "react";
import { runManager } from "../services/runManager";
import { DiscoveryGroupResult, DiscoveryHorizon } from "../api/discovery";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, ReferenceLine } from "recharts";
import { ScoreCell, ScoreExplanationBanner } from "../components/ExplanationBanner";

function ScoreBar({ score }: { score: number | null | undefined }) {
  if (score == null) return null;
  return (
    <div style={{ height: "6px", background: "#27272a", borderRadius: "3px", overflow: "hidden", margin: "14px 0 20px 0" }}>
      <div style={{
        height: "100%",
        width: `${Math.max(0, Math.min(100, score))}%`,
        background: score >= 75 ? "#10b981" : score < 50 ? "#f43f5e" : "#f59e0b",
        transition: "width 0.5s ease-out"
      }} />
    </div>
  );
}

function MetricTile({ label, value, subtext, color }: { label: string; value: string | number; subtext?: string; color?: string }) {
  return (
    <div className="metric-tile">
      <div className="metric-tile-label">{label}</div>
      <div className="metric-tile-value" style={color ? { color } : {}}>
        {value}
      </div>
      {subtext && <div className="metric-tile-sublabel">{subtext}</div>}
    </div>
  );
}

function ConsistencyChart({ periods }: { periods: any[] }) {
  if (!periods || periods.length === 0) return null;
  const data = periods.map((p, i) => ({
    name: `Period ${i+1}`,
    company: p.median_company_return,
    benchmark: p.median_benchmark_return
  }));
  
  return (
    <div style={{ height: 280, width: '100%', marginTop: '24px', background: "#18181b", padding: "16px", borderRadius: "12px", border: "1px solid #27272a" }}>
      <h4 style={{ marginBottom: '12px', color: '#ffffff' }}>📊 Historical 5-Period Median Returns vs Benchmark</h4>
      <ResponsiveContainer width="100%" height="80%">
        <BarChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
          <XAxis dataKey="name" stroke="#a1a1aa" tick={{fontSize: 12}} />
          <YAxis stroke="#a1a1aa" tick={{fontSize: 12}} tickFormatter={(val) => `${val}%`} />
          <Tooltip 
             contentStyle={{ background: '#121215', borderColor: '#27272a', borderRadius: '8px', color: '#ffffff' }}
             formatter={(value: any) => `${Number(value).toFixed(2)}%`}
          />
          <Legend wrapperStyle={{ color: "#a1a1aa", fontSize: "0.85rem" }} />
          <ReferenceLine y={0} stroke="#3f3f46" />
          <Bar dataKey="company" name="Median Constituent Return" fill="#ffffff" radius={[4, 4, 0, 0]} />
          <Bar dataKey="benchmark" name="Median Benchmark Return" fill="#71717a" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function GroupDetailsPage() {
  const { runId, type, name } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const horizon = searchParams.get("horizon") || "SHORT";
  const parentSector = searchParams.get("parentSector") || "";
  const parentIndustry = searchParams.get("parentIndustry") || "";

  const [constituents, setConstituents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  const result = runManager.getState().result;
  let groupDetails: DiscoveryGroupResult | undefined;
  if (result && result.horizons[horizon as DiscoveryHorizon]) {
    const horizonData = result.horizons[horizon as DiscoveryHorizon];
    if (type === "SECTOR") {
      groupDetails = horizonData.sectors.find((s: any) => s.name === name);
    } else if (type === "INDUSTRY") {
      groupDetails = horizonData.industries.find((i: any) => i.name === name && (i.parent_sector || "") === parentSector);
    } else if (type === "BASIC_INDUSTRY") {
      groupDetails = horizonData.basic_industries.find((b: any) => b.name === name && (b.parent_industry || "") === parentIndustry && (b.parent_sector || "") === parentSector);
    }
  }
  const tech = groupDetails?.tech_details;
  const fund = groupDetails?.fund_details;
  const macro = groupDetails?.macro_details;

  useEffect(() => {
    let active = true;
    const fetchConstituents = async () => {
      setLoading(true);
      try {
        const url = `/api/v1/discovery/runs/${runId}/constituents?horizon=${horizon}&entity_type=${type}&entity_name=${encodeURIComponent(name || "")}&parent_sector=${encodeURIComponent(parentSector)}&parent_industry=${encodeURIComponent(parentIndustry)}`;
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
  }, [runId, type, name, horizon, parentSector, parentIndustry]);

  useEffect(() => {
    if (runId && runManager.getState().activeRunId !== runId) {
      runManager.loadResult(runId).catch(console.error);
    }
  }, [runId]);

  if (!groupDetails) {
    if (runManager.getState().flowState === "LOADING_RESULT") return <div className="empty-state">Loading group details...</div>;
    return <div className="panel error">Group details not found for {name}</div>;
  }

  const filteredConstituents = constituents.filter((c) =>
    c.symbol.toLowerCase().includes(filter.toLowerCase()) ||
    (c.sector && c.sector.toLowerCase().includes(filter.toLowerCase())) ||
    (c.industry && c.industry.toLowerCase().includes(filter.toLowerCase()))
  );

  const horizonLabel = horizon === "LONG" ? "1 Month (1M)" : horizon === "MID" ? "1 Week (1W)" : "1 Day (1D)";

  return (
    <div className="discovery-shell">
      {/* Page Navigation Header */}
      <header className="dashboard-hero" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '20px' }}>
        <div>
          <button onClick={() => navigate(-1)} className="secondary" style={{ padding: '6px 14px', height: '32px', fontSize: '0.82rem', marginBottom: '10px' }}>
            &larr; Back to Discovery Results
          </button>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <h1 style={{ margin: 0 }}>{name}</h1>
            <span className="badge pending" style={{ fontSize: "0.75rem" }}>🎯 {horizonLabel}</span>
          </div>
          <p className="eyebrow" style={{ color: "var(--text-muted)", fontSize: "0.82rem", textTransform: "uppercase", marginTop: "4px" }}>
            {(type || '').replace(/_/g, ' ')} ANALYSIS {parentSector ? `• ${parentSector}` : ''}
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Composite Score</div>
            <ScoreCell score={groupDetails.final_score} />
          </div>
        </div>
      </header>

      <ScoreExplanationBanner />

      {/* Breakdown Cards Section */}
      {groupDetails && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Technical Card */}
          <div className="panel run-card">
            <div className="run-card-header">
              <div>
                <h3>Technical Analysis Breakdown</h3>
                <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", marginTop: "2px" }}>
                  Price relative returns, breadth indicators & 5-period momentum consistency.
                </div>
              </div>
              <ScoreCell score={groupDetails.technical_score} />
            </div>
            <ScoreBar score={groupDetails.technical_score} />

            <div className="metric-grid">
              <MetricTile 
                label="Relative Return (Median)" 
                value={tech?.median_relative_return != null ? `${tech.median_relative_return >= 0 ? '+' : ''}${tech.median_relative_return.toFixed(1)}%` : 'N/A'}
                subtext="Outperformance vs Benchmark"
                color={tech?.median_relative_return >= 0 ? "#10b981" : "#f43f5e"}
              />
              <MetricTile 
                label="Positive Return Ratio" 
                value={tech?.positive_return_breadth != null ? `${tech.positive_return_breadth.toFixed(1)}%` : 'N/A'}
                subtext="% constituents with positive gain"
              />
              <MetricTile 
                label="Outperformance Ratio" 
                value={tech?.outperformance_breadth != null ? `${tech.outperformance_breadth.toFixed(1)}%` : 'N/A'}
                subtext="% constituents beating index"
              />
              <MetricTile 
                label="High Consistency Rate" 
                value={tech?.percent_consistency_gte_60 != null ? `${tech.percent_consistency_gte_60.toFixed(1)}%` : 'N/A'}
                subtext="% stocks with ≥60% consistency"
              />
            </div>
            
            <ConsistencyChart 
              periods={
                 tech?.consistency_periods || 
                 tech?.technical?.consistency?.consistency_periods 
              } 
            />
          </div>

          {/* Fundamental Card */}
          <div className="panel run-card">
            <div className="run-card-header">
              <div>
                <h3>Fundamental Analysis Breakdown</h3>
                <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", marginTop: "2px" }}>
                  Sales growth, operating margins, leverage safety & earnings stability across member stocks.
                </div>
              </div>
              <ScoreCell score={groupDetails.fundamental_score} />
            </div>
            <ScoreBar score={groupDetails.fundamental_score} />

            <div className="metric-grid">
              {fund?.metrics?.sales_growth_pct?.median != null && (
                <MetricTile label="Sales Growth (Median)" value={`${fund.metrics.sales_growth_pct.median.toFixed(1)}%`} subtext="Year-over-year revenue" />
              )}
              {fund?.metrics?.net_profit_growth_pct?.median != null && (
                <MetricTile label="Net Profit Growth" value={`${fund.metrics.net_profit_growth_pct.median.toFixed(1)}%`} subtext="Bottom-line earnings" />
              )}
              {fund?.metrics?.positive_pat_period_ratio?.median != null && (
                <MetricTile label="Profitable History Ratio" value={`${fund.metrics.positive_pat_period_ratio.median.toFixed(1)}%`} subtext="% periods with net profit" />
              )}
              {fund?.metrics?.latest_operating_margin_pct?.median != null && (
                <MetricTile label="Operating Margin" value={`${fund.metrics.latest_operating_margin_pct.median.toFixed(1)}%`} subtext="Operating efficiency" />
              )}
              {fund?.metrics?.operating_margin_change_pp?.median != null && (
                <MetricTile label="Margin Expansion" value={`${fund.metrics.operating_margin_change_pp.median.toFixed(1)} pp`} subtext="Margin trend" />
              )}
              {fund?.metrics?.pat_growth_volatility_pct?.median != null && (
                <MetricTile label="PAT Volatility Index" value={`${fund.metrics.pat_growth_volatility_pct.median.toFixed(1)}%`} subtext="Earnings stability" />
              )}
              {fund?.metrics?.debt_to_equity?.median != null && (
                <MetricTile label="Debt-to-Equity (Median)" value={fund.metrics.debt_to_equity.median.toFixed(2)} subtext="Financial leverage" />
              )}
              {fund?.metrics?.borrowing_change_pct?.median != null && (
                <MetricTile label="Borrowing Change" value={`${fund.metrics.borrowing_change_pct.median.toFixed(1)}%`} subtext="Debt trend" />
              )}
              {fund?.metrics?.latest_ocf_to_pat?.median != null && (
                <MetricTile label="OCF to PAT Ratio" value={fund.metrics.latest_ocf_to_pat.median.toFixed(2)} subtext="Cash flow quality" />
              )}
            </div>

            {/* Pillar Scores Bar */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", marginTop: "16px", paddingTop: "14px", borderTop: "1px solid var(--panel-border)" }}>
              {fund?.pillar_scores?.growth?.score != null && (
                <div className="pillar-chip">
                  <span style={{ color: "var(--text-muted)" }}>Growth Pillar:</span>
                  <span style={{ color: fund.pillar_scores.growth.score >= 50 ? "#10b981" : "#f43f5e" }}>{fund.pillar_scores.growth.score.toFixed(1)} / 100</span>
                </div>
              )}
              {fund?.pillar_scores?.profitability?.score != null && (
                <div className="pillar-chip">
                  <span style={{ color: "var(--text-muted)" }}>Profitability Pillar:</span>
                  <span style={{ color: fund.pillar_scores.profitability.score >= 50 ? "#10b981" : "#f43f5e" }}>{fund.pillar_scores.profitability.score.toFixed(1)} / 100</span>
                </div>
              )}
              {fund?.pillar_scores?.financial_strength?.score != null && (
                <div className="pillar-chip">
                  <span style={{ color: "var(--text-muted)" }}>Financial Strength:</span>
                  <span style={{ color: fund.pillar_scores.financial_strength.score >= 50 ? "#10b981" : "#f43f5e" }}>{fund.pillar_scores.financial_strength.score.toFixed(1)} / 100</span>
                </div>
              )}
              {fund?.pillar_scores?.earnings_quality?.score != null && (
                <div className="pillar-chip">
                  <span style={{ color: "var(--text-muted)" }}>Earnings Quality:</span>
                  <span style={{ color: fund.pillar_scores.earnings_quality.score >= 50 ? "#10b981" : "#f43f5e" }}>{fund.pillar_scores.earnings_quality.score.toFixed(1)} / 100</span>
                </div>
              )}
            </div>
          </div>

          {/* Macro Analysis Card */}
          <div className="panel run-card">
            <div className="run-card-header">
              <div>
                <h3>Macro Analysis Breakdown</h3>
                <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", marginTop: "2px" }}>
                  Macroeconomic trends, sector policy alignment & market sentiment.
                </div>
              </div>
              <ScoreCell score={groupDetails.macro_score} />
            </div>
            <ScoreBar score={groupDetails.macro_score} />

            <div className="metric-grid">
              {macro?.categories ? Object.entries(macro.categories).map(([catKey, catVal]: [string, any]) => (
                <MetricTile 
                  key={catKey}
                  label={catKey.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ')}
                  value={catVal.impact}
                  subtext={catVal.numeric_value != null ? `${catVal.numeric_value} pts` : undefined}
                  color={catVal.impact === 'POSITIVE' ? '#10b981' : catVal.impact === 'NEGATIVE' ? '#f43f5e' : '#f59e0b'}
                />
              )) : (
                <div style={{ padding: "12px", background: "#18181b", borderRadius: "8px", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
                  Sector aligned with macroeconomic trends & market conditions.
                </div>
              )}
            </div>

            {macro && (
              <div style={{ marginTop: "14px", padding: "14px", background: "#18181b", borderRadius: "8px", border: "1px solid #27272a" }}>
                <span className={`badge ${macro?.llm_overall_impact === 'POSITIVE' ? 'completed' : macro?.llm_overall_impact === 'NEGATIVE' ? 'error' : 'pending'}`}>
                  {macro?.llm_overall_impact || "NEUTRAL"} OUTLOOK
                </span>
                <p style={{ margin: "8px 0 0 0", fontSize: "0.88rem", color: "#f4f4f5" }}>
                  {macro?.llm_summary || macro?.reasoning || "Macro parameters align favorably with standard sector metrics."}
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Constituents table */}
      <section className="panel results-panel" style={{ marginTop: "20px" }}>
        <div className="table-filter-bar">
          <div>
            <h2>Constituent Companies</h2>
            <div style={{ fontSize: "0.85rem", color: "var(--text-secondary)", marginTop: "2px" }}>
              Click on any stock to inspect detailed fundamental ratios and technical charts.
            </div>
          </div>
          <div className="search-input-wrap">
            <span className="search-icon">🔍</span>
            <input
              type="text"
              placeholder="Search constituent by symbol..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
        </div>

        {loading ? (
          <div className="empty-state">Loading constituent stocks...</div>
        ) : filteredConstituents.length === 0 ? (
          <div className="empty-state">No matching constituents found.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Sector</th>
                  <th>Industry</th>
                  <th>Technical Score (0-100)</th>
                  <th>Fundamental Score (0-100)</th>
                </tr>
              </thead>
              <tbody>
                {filteredConstituents.map((c) => (
                  <tr 
                    key={c.symbol} 
                    onClick={() => navigate(`/discovery/${runId}/stock/${encodeURIComponent(c.symbol)}?horizon=${horizon}`)} 
                    className="clickable-row"
                  >
                    <td style={{ fontWeight: 700, fontSize: "1.05rem", color: "#ffffff" }}>{c.symbol}</td>
                    <td>{c.sector}</td>
                    <td>{c.industry}</td>
                    <td>
                      <ScoreCell score={c.technical_score} />
                    </td>
                    <td>
                      <ScoreCell score={c.fundamental_score} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
