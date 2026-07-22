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

  return (
    <div className="discovery-shell">
      <header className="dashboard-hero" style={{ display: 'flex', gap: '20px', alignItems: 'center' }}>
        <button onClick={() => navigate(-1)} className="secondary" style={{ padding: '8px 18px', height: '40px', flexShrink: 0 }}>&larr; Back to Results</button>
        <div>
          <p className="eyebrow" style={{ color: "var(--text-muted)", fontSize: "0.8rem", textTransform: "uppercase" }}>
            {(type || '').replace(/_/g, ' ')} ANALYSIS • {horizon} TERM
          </p>
          <h1 style={{ margin: "2px 0 0 0" }}>{name}</h1>
        </div>
      </header>

      <ScoreExplanationBanner />

      {/* Cards section */}
      {groupDetails && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Technical Card */}
          <div className="panel run-card">
            <div className="run-card-header">
              <div>
                <h3>Technical Analysis Breakdown</h3>
                <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", marginTop: "2px" }}>
                  Evaluates 5-period price relative return, breadth metrics, and trend consistency.
                </div>
              </div>
              <ScoreCell score={groupDetails.technical_score} />
            </div>
            <ScoreBar score={groupDetails.technical_score} />

            <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px' }}>
              <div className="run-card-section">
                <h4>1. Returns Performance</h4>
                <ul className="run-card-list">
                  <li>Median Relative Return: <strong>{tech?.median_relative_return != null ? tech.median_relative_return.toFixed(1) + '%' : '-'}</strong></li>
                  <li>Pillar Score: <strong style={{ color: (tech?.scores?.return_score || 0) >= 50 ? "#10b981" : "#f43f5e" }}>{tech?.scores?.return_score != null ? tech.scores.return_score.toFixed(1) + ' / 100' : '-'}</strong></li>
                </ul>
              </div>

              <div className="run-card-section">
                <h4>2. Market Breadth</h4>
                <ul className="run-card-list">
                  <li>Positive Return Ratio: <strong>{tech?.positive_return_breadth != null ? tech.positive_return_breadth.toFixed(1) + '%' : '-'}</strong></li>
                  <li>Outperforming Benchmark: <strong>{tech?.outperformance_breadth != null ? tech.outperformance_breadth.toFixed(1) + '%' : '-'}</strong></li>
                  <li>Pillar Score: <strong style={{ color: (tech?.scores?.breadth || 0) >= 50 ? "#10b981" : "#f43f5e" }}>{tech?.scores?.breadth != null ? tech.scores.breadth.toFixed(1) + ' / 100' : '-'}</strong></li>
                </ul>
              </div>

              <div className="run-card-section">
                <h4>3. Trend Consistency</h4>
                <ul className="run-card-list">
                  <li>High Consistency Rate (&ge;60%): <strong>{tech?.percent_consistency_gte_60 != null ? tech.percent_consistency_gte_60.toFixed(1) + '%' : '-'}</strong></li>
                  <li>Pillar Score: <strong style={{ color: (tech?.scores?.consistency || 0) >= 50 ? "#10b981" : "#f43f5e" }}>{tech?.scores?.consistency != null ? tech.scores.consistency.toFixed(1) + ' / 100' : '-'}</strong></li>
                </ul>
              </div>
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
                  Evaluates sales growth, operating margins, debt safety & earnings quality.
                </div>
              </div>
              <ScoreCell score={groupDetails.fundamental_score} />
            </div>
            <ScoreBar score={groupDetails.fundamental_score} />

            <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px' }}>
              <div className="run-card-section">
                <h4>1. Revenue & Profit Growth</h4>
                <ul className="run-card-list">
                  <li>Sales Growth (Median): <strong>{fund?.metrics?.sales_growth_pct?.median != null ? fund.metrics.sales_growth_pct.median.toFixed(1) + '%' : '-'}</strong></li>
                  <li>Net Profit Growth (Median): <strong>{fund?.metrics?.net_profit_growth_pct?.median != null ? fund.metrics.net_profit_growth_pct.median.toFixed(1) + '%' : '-'}</strong></li>
                  <li>Pillar Score: <strong style={{ color: (fund?.pillar_scores?.growth?.score || 0) >= 50 ? "#10b981" : "#f43f5e" }}>{fund?.pillar_scores?.growth?.score != null ? fund.pillar_scores.growth.score.toFixed(1) + ' / 100' : '-'}</strong></li>
                </ul>
              </div>

              <div className="run-card-section">
                <h4>2. Profitability Margins</h4>
                <ul className="run-card-list">
                  <li>Operating Margin (Median): <strong>{fund?.metrics?.latest_operating_margin_pct?.median != null ? fund.metrics.latest_operating_margin_pct.median.toFixed(1) + '%' : '-'}</strong></li>
                  <li>Margin Change: <strong>{fund?.metrics?.operating_margin_change_pp?.median != null ? fund.metrics.operating_margin_change_pp.median.toFixed(1) + ' pp' : '-'}</strong></li>
                  <li>Pillar Score: <strong style={{ color: (fund?.pillar_scores?.profitability?.score || 0) >= 50 ? "#10b981" : "#f43f5e" }}>{fund?.pillar_scores?.profitability?.score != null ? fund.pillar_scores.profitability.score.toFixed(1) + ' / 100' : '-'}</strong></li>
                </ul>
              </div>

              <div className="run-card-section">
                <h4>3. Financial Health & Debt</h4>
                <ul className="run-card-list">
                  <li>Debt-to-Equity (Median): <strong>{fund?.metrics?.debt_to_equity?.median != null ? fund.metrics.debt_to_equity.median.toFixed(2) : '-'}</strong></li>
                  <li>Pillar Score: <strong style={{ color: (fund?.pillar_scores?.financial_strength?.score || 0) >= 50 ? "#10b981" : "#f43f5e" }}>{fund?.pillar_scores?.financial_strength?.score != null ? fund.pillar_scores.financial_strength.score.toFixed(1) + ' / 100' : '-'}</strong></li>
                </ul>
              </div>
            </div>
          </div>

          {/* Macro Card */}
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

            <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px' }}>
              <div className="run-card-section">
                <h4>Economic Indicators</h4>
                <ul className="run-card-list">
                  {macro?.categories ? Object.entries(macro.categories).map(([catKey, catVal]: [string, any]) => (
                    <li key={catKey}>
                      <span style={{ fontWeight: 600 }}>{catKey.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ')}:</span>
                      <span className={catVal.impact === 'POSITIVE' ? 'score-high' : catVal.impact === 'NEGATIVE' ? 'score-low' : 'score-mid'}>
                        {catVal.impact} ({catVal.numeric_value ?? '-'} pts)
                      </span>
                    </li>
                  )) : <li>No specific indicators listed for this sector.</li>}
                </ul>
              </div>
              {macro && (
                <div className="run-card-section">
                  <h4>Macro Sentiment & Summary</h4>
                  <div style={{ padding: "12px", background: "#18181b", borderRadius: "8px", border: "1px solid #27272a" }}>
                    <span className={`badge ${macro.llm_overall_impact === 'POSITIVE' ? 'completed' : macro.llm_overall_impact === 'NEGATIVE' ? 'error' : 'warning'}`}>
                      {macro.llm_overall_impact || "NEUTRAL"} OUTLOOK
                    </span>
                    <p style={{ margin: "8px 0 0 0", fontSize: "0.85rem", color: "var(--text-secondary)" }}>
                      {macro.reasoning || "Macro parameters align favorably with standard sector metrics."}
                    </p>
                  </div>
                </div>
              )}
            </div>
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
