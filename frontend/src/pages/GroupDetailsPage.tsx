import { useEffect, useState } from "react";
import { runManager } from "../services/runManager";
import { DiscoveryGroupResult, DiscoveryHorizon } from "../api/discovery";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, ReferenceLine } from "recharts";

function ScoreBar({ score }: { score: number | null | undefined }) {
  if (score == null) return null;
  return (
    <div className="score-bar-bg" style={{ height: "4px", background: "var(--panel-border)", borderRadius: "2px", overflow: "hidden", margin: "16px 0" }}>
      <div style={{
        height: "100%",
        width: `${Math.max(0, Math.min(100, score))}%`,
        background: score >= 70 ? "var(--completed)" : score < 40 ? "var(--error)" : "var(--warning)",
        transition: "width 0.5s ease-out, background 0.3s"
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
    <div style={{ height: 300, width: '100%', marginTop: '32px' }}>
      <h4 style={{ marginBottom: '16px' }}>Consistency Breakdown (Medians)</h4>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
          <XAxis dataKey="name" stroke="var(--text-secondary)" tick={{fontSize: 12}} />
          <YAxis stroke="var(--text-secondary)" tick={{fontSize: 12}} tickFormatter={(val) => `${val}%`} />
          <Tooltip 
             contentStyle={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)', borderRadius: '8px' }}
             formatter={(value: any) => `${Number(value).toFixed(2)}%`}
          />
          <Legend />
          <ReferenceLine y={0} stroke="var(--panel-border)" />
          <Bar dataKey="company" name="Median Constituent Return" fill="var(--primary)" radius={[4, 4, 0, 0]} />
          <Bar dataKey="benchmark" name="Median Benchmark Return" fill="var(--text-secondary)" radius={[4, 4, 0, 0]} />
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
    } else if (runId && runManager.getState().flowState === "IDLE") {
      runManager.loadResult(runId).catch(console.error);
    }
  }, [runId]);

  if (!groupDetails) {
    if (runManager.getState().flowState === "LOADING_RESULT") return <div className="empty-state">Loading group details...</div>;
    return <div className="panel error">Group details not found for {name}</div>;
  }

  return (
    <div className="discovery-shell">
      <header className="page-header" style={{ display: 'flex', gap: '24px', alignItems: 'center' }}>
        <button onClick={() => navigate(-1)} className="secondary" style={{ padding: '8px 16px', height: '40px', background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>&larr; Back</button>
        <div>
          <p className="eyebrow">
            {type === 'SECTOR' ? 'Sector' : type === 'INDUSTRY' ? `${parentSector} > Industry` : `${parentSector} > ${parentIndustry} > Basic Industry`}
          </p>
          <h1>{name}</h1>
        </div>
      </header>
      
      {/* Cards are above the Companies table */}
      {groupDetails && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', marginBottom: '24px' }}>
          
                    {/* Technical Card */}
          <div className="panel run-card">
            <div className="run-card-header">
              <h3>Technical Details</h3>
              <span className={`badge ${groupDetails.technical_score && groupDetails.technical_score >= 70 ? "completed" : groupDetails.technical_score && groupDetails.technical_score < 40 ? "error" : "warning"}`}>
                Score: {groupDetails.technical_score !== null && groupDetails.technical_score !== undefined ? groupDetails.technical_score.toFixed(1) : "-"}
              </span>
            </div>
            <ScoreBar score={groupDetails.technical_score} />
            <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div className="run-card-section">
                <h4>1. Returns</h4>
                <ul className="run-card-list">
                  <li><span className="run-card-rank">MR</span> <span>Median Ret: <span>{tech?.median_relative_return != null ? tech.median_relative_return.toFixed(1) + '%' : '-'}</span></span></li>
                  <li style={{ marginTop: '8px' }}><span className="run-card-rank" style={{ background: 'transparent', border: '1px solid var(--panel-border)' }}>★</span> <strong style={{ color: 'var(--text-primary)' }}>Return Score: <span className={(tech?.scores?.return_score || 0) >= 50 ? "score-high" : "score-low"}>{tech?.scores?.return_score != null ? tech.scores.return_score.toFixed(1) : '-'}</span></strong></li>
                </ul>
              </div>
              <div className="run-card-section">
                <h4>2. Breadth</h4>
                <ul className="run-card-list">
                  <li><span className="run-card-rank">PB</span> <span>Positive Ret: <span>{tech?.positive_return_breadth != null ? tech.positive_return_breadth.toFixed(1) + '%' : '-'}</span></span></li>
                  <li><span className="run-card-rank">OB</span> <span>Outperform: <span>{tech?.outperformance_breadth != null ? tech.outperformance_breadth.toFixed(1) + '%' : '-'}</span></span></li>
                  <li style={{ marginTop: '8px' }}><span className="run-card-rank" style={{ background: 'transparent', border: '1px solid var(--panel-border)' }}>★</span> <strong style={{ color: 'var(--text-primary)' }}>Breadth Score: <span className={(tech?.scores?.breadth || 0) >= 50 ? "score-high" : "score-low"}>{tech?.scores?.breadth != null ? tech.scores.breadth.toFixed(1) : '-'}</span></strong></li>
                </ul>
              </div>
              <div className="run-card-section">
                <h4>3. Volume</h4>
                <ul className="run-card-list">
                  <li style={{ marginTop: '8px' }}><span className="run-card-rank" style={{ background: 'transparent', border: '1px solid var(--panel-border)' }}>★</span> <strong style={{ color: 'var(--text-primary)' }}>Volume Score: <span className={(tech?.scores?.volume || 0) >= 50 ? "score-high" : "score-low"}>{tech?.scores?.volume != null ? tech.scores.volume.toFixed(1) : '-'}</span></strong></li>
                </ul>
              </div>
              <div className="run-card-section">
                <h4>4. Consistency</h4>
                <ul className="run-card-list">
                  <li><span className="run-card-rank">CS</span> <span>{'>= 60% Cons.:'} <span>{tech?.percent_consistency_gte_60 != null ? tech.percent_consistency_gte_60.toFixed(1) + '%' : '-'}</span></span></li>
                  <li style={{ marginTop: '8px' }}><span className="run-card-rank" style={{ background: 'transparent', border: '1px solid var(--panel-border)' }}>★</span> <strong style={{ color: 'var(--text-primary)' }}>Consistency Score: <span className={(tech?.scores?.consistency || 0) >= 50 ? "score-high" : "score-low"}>{tech?.scores?.consistency != null ? tech.scores.consistency.toFixed(1) : '-'}</span></strong></li>
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
              <h3>Fundamental Details</h3>
              <span className={`badge ${groupDetails.fundamental_score && groupDetails.fundamental_score >= 70 ? "completed" : groupDetails.fundamental_score && groupDetails.fundamental_score < 40 ? "error" : "warning"}`}>
                Score: {groupDetails.fundamental_score !== null && groupDetails.fundamental_score !== undefined ? groupDetails.fundamental_score.toFixed(1) : "-"}
              </span>
            </div>
            <ScoreBar score={groupDetails.fundamental_score} />
            <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div className="run-card-section">
                <h4>1. Growth</h4>
                <ul className="run-card-list">
                  <li><span className="run-card-rank">SG</span> <span>Sales Gr: <span>{fund?.metrics?.sales_growth_pct?.median != null ? fund.metrics.sales_growth_pct.median.toFixed(1) + '%' : '-'}</span></span></li>
                  <li><span className="run-card-rank">NG</span> <span>Net Profit Gr: <span>{fund?.metrics?.net_profit_growth_pct?.median != null ? fund.metrics.net_profit_growth_pct.median.toFixed(1) + '%' : '-'}</span></span></li>
                  <li style={{ marginTop: '8px' }}><span className="run-card-rank" style={{ background: 'transparent', border: '1px solid var(--panel-border)' }}>★</span> <strong style={{ color: 'var(--text-primary)' }}>Growth Score: <span className={(fund?.pillar_scores?.growth?.score || 0) >= 50 ? "score-high" : "score-low"}>{fund?.pillar_scores?.growth?.score != null ? fund.pillar_scores.growth.score.toFixed(1) : '-'}</span></strong></li>
                </ul>
              </div>
              <div className="run-card-section">
                <h4>2. Profitability</h4>
                <ul className="run-card-list">
                  <li><span className="run-card-rank">OM</span> <span>Op Margin: <span>{fund?.metrics?.latest_operating_margin_pct?.median != null ? fund.metrics.latest_operating_margin_pct.median.toFixed(1) + '%' : '-'}</span></span></li>
                  <li><span className="run-card-rank">MT</span> <span>Margin Trend: <span>{fund?.metrics?.operating_margin_change_pp?.median != null ? fund.metrics.operating_margin_change_pp.median.toFixed(1) + ' pp' : '-'}</span></span></li>
                  <li style={{ marginTop: '8px' }}><span className="run-card-rank" style={{ background: 'transparent', border: '1px solid var(--panel-border)' }}>★</span> <strong style={{ color: 'var(--text-primary)' }}>Profitability Score: <span className={(fund?.pillar_scores?.profitability?.score || 0) >= 50 ? "score-high" : "score-low"}>{fund?.pillar_scores?.profitability?.score != null ? fund.pillar_scores.profitability.score.toFixed(1) : '-'}</span></strong></li>
                </ul>
              </div>
              <div className="run-card-section">
                <h4>3. Fin. Strength</h4>
                <ul className="run-card-list">
                  <li><span className="run-card-rank">DE</span> <span>Debt/Equity: <span>{fund?.metrics?.debt_to_equity?.median != null ? fund.metrics.debt_to_equity.median.toFixed(2) : '-'}</span></span></li>
                  <li><span className="run-card-rank">BC</span> <span>Borrowing Chg: <span>{fund?.metrics?.borrowing_change_pct?.median != null ? fund.metrics.borrowing_change_pct.median.toFixed(1) + '%' : '-'}</span></span></li>
                  <li style={{ marginTop: '8px' }}><span className="run-card-rank" style={{ background: 'transparent', border: '1px solid var(--panel-border)' }}>★</span> <strong style={{ color: 'var(--text-primary)' }}>Strength Score: <span className={(fund?.pillar_scores?.financial_strength?.score || 0) >= 50 ? "score-high" : "score-low"}>{fund?.pillar_scores?.financial_strength?.score != null ? fund.pillar_scores.financial_strength.score.toFixed(1) : '-'}</span></strong></li>
                </ul>
              </div>
              <div className="run-card-section">
                <h4>4. Earn Quality</h4>
                <ul className="run-card-list">
                  <li><span className="run-card-rank">OC</span> <span>OCF to PAT: <span>{fund?.metrics?.latest_ocf_to_pat?.median != null ? fund.metrics.latest_ocf_to_pat.median.toFixed(2) : '-'}</span></span></li>
                  <li><span className="run-card-rank">PV</span> <span>Profit Volatility: <span>{fund?.metrics?.pat_growth_volatility_pct?.median != null ? fund.metrics.pat_growth_volatility_pct.median.toFixed(1) + '%' : '-'}</span></span></li>
                  <li style={{ marginTop: '8px' }}><span className="run-card-rank" style={{ background: 'transparent', border: '1px solid var(--panel-border)' }}>★</span> <strong style={{ color: 'var(--text-primary)' }}>Quality Score: <span className={(fund?.pillar_scores?.earnings_quality?.score || 0) >= 50 ? "score-high" : "score-low"}>{fund?.pillar_scores?.earnings_quality?.score != null ? fund.pillar_scores.earnings_quality.score.toFixed(1) : '-'}</span></strong></li>
                </ul>
              </div>
            </div>
          </div>

          {/* Macro Card */}
          <div className="panel run-card">
            <div className="run-card-header">
              <h3>Macro Analysis</h3>
              <span className={`badge ${groupDetails.macro_score && groupDetails.macro_score >= 70 ? "completed" : groupDetails.macro_score && groupDetails.macro_score < 40 ? "error" : "warning"}`}>
                Score: {groupDetails.macro_score !== null && groupDetails.macro_score !== undefined ? groupDetails.macro_score.toFixed(1) : "-"}
              </span>
            </div>
            <ScoreBar score={groupDetails.macro_score} />
            <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
              <div className="run-card-section">
                <h4>Economic Indicators</h4>
                <ul className="run-card-list">
                  {macro?.categories ? Object.entries(macro.categories).map(([catKey, catVal]: [string, any]) => (
                    <li key={catKey}>
                      <span className="run-card-rank">{catKey.substring(0, 2).toUpperCase()}</span>
                      <span>
                        {catKey.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ')}: 
                        <span className={catVal.impact === 'POSITIVE' ? 'score-high' : catVal.impact === 'NEGATIVE' ? 'score-low' : 'score-mid'} style={{ marginLeft: '4px' }}>
                          {catVal.impact} ({catVal.numeric_value ?? '-'} pts)
                        </span>
                      </span>
                    </li>
                  )) : <li>No indicators available.</li>}
                </ul>
              </div>
              {macro && (
                <div className="run-card-section">
                  <h4>Overall Outlook</h4>
                  <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <span className={`badge ${macro.llm_overall_impact === 'POSITIVE' ? 'completed' : macro.llm_overall_impact === 'NEGATIVE' ? 'error' : 'warning'}`}>
                        {macro.llm_overall_impact || "N/A"}
                      </span>
                      <strong>{groupDetails.name}</strong>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

        </div>
      )}

      <section className="panel results-panel">
        <div className="panel-title">
          <h2>Companies</h2>
        </div>
        {loading ? (
          <div className="empty-state">Loading constituents...</div>
        ) : constituents.length === 0 ? (
          <div className="empty-state">No constituents found.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Sector</th>
                  <th>Industry</th>
                  <th>Technical Score</th>
                  <th>Fundamental Score</th>
                </tr>
              </thead>
              <tbody>
                {constituents.map((c) => (
                  <tr 
                    key={c.symbol} 
                    onClick={() => navigate(`/discovery/${runId}/stock/${encodeURIComponent(c.symbol)}?horizon=${horizon}`)} 
                    style={{ cursor: 'pointer' }} 
                    className="clickable-row"
                  >
                    <td style={{ fontWeight: 600, color: 'var(--accent-primary)' }}>{c.symbol}</td>
                    <td>{c.sector}</td>
                    <td>{c.industry}</td>
                    <td className={c.technical_score >= 70 ? "score-high" : c.technical_score < 40 ? "score-low" : "score-mid"}>
                      {c.technical_score !== null ? c.technical_score.toFixed(1) : "-"}
                    </td>
                    <td className={c.fundamental_score >= 70 ? "score-high" : c.fundamental_score < 40 ? "score-low" : "score-mid"}>
                      {c.fundamental_score !== null ? c.fundamental_score.toFixed(1) : "-"}
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
