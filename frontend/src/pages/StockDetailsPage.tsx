import { useEffect, useState } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, ReferenceLine } from "recharts";
function ScoreBar({ score }: { score: number | null }) {
  if (score === null || isNaN(score)) return <div style={{ background: 'var(--panel-border)', height: '6px', borderRadius: '3px', width: '100%', marginBottom: '16px' }} />;
  const width = `${Math.min(Math.max(score, 0), 100)}%`;
  const color = score >= 70 ? 'var(--success)' : score < 40 ? 'var(--danger)' : 'var(--warning)';
  
  return (
    <div style={{ background: 'var(--panel-border)', height: '6px', borderRadius: '3px', width: '100%', overflow: 'hidden', marginBottom: '16px' }}>
      <div style={{ width, background: color, height: '100%', transition: 'width 1s ease-out' }} />
    </div>
  );
}


function MiniBarChart({ score }: { score: number | undefined | null }) {
  if (score === undefined || score === null || isNaN(score)) return null;
  const width = `${Math.min(Math.max(score, 0), 100)}%`;
  const color = score >= 70 ? 'var(--success)' : score < 40 ? 'var(--danger)' : 'var(--warning)';
  return (
    <div style={{ display: 'inline-block', width: '60px', height: '6px', background: 'var(--panel-border)', borderRadius: '3px', overflow: 'hidden', marginLeft: '8px', verticalAlign: 'middle' }}>
      <div style={{ width, background: color, height: '100%' }} />
    </div>
  );
}

function ConsistencyChart({ periods }: { periods: any[] }) {
  if (!periods || periods.length === 0) return null;
  const data = periods.map((p, i) => ({
    name: `Period ${i+1}`,
    company: p.company_return,
    benchmark: p.benchmark_return
  }));
  
  return (
    <div style={{ height: 300, width: '100%', marginTop: '32px' }}>
      <h4 style={{ marginBottom: '16px' }}>Consistency Breakdown</h4>
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
          <Bar dataKey="company" name="Company Return" fill="var(--primary)" radius={[4, 4, 0, 0]} />
          <Bar dataKey="benchmark" name="Benchmark Return" fill="var(--text-secondary)" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function StockDetailsPage() {
  const { runId, symbol } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const horizon = searchParams.get("horizon") || "SHORT";

  const [constituent, setConstituents] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const fetchConstituent = async () => {
      setLoading(true);
      try {
        const url = `/api/v1/discovery/runs/${runId}/constituents?horizon=${horizon}&entity_type=STOCK&entity_name=${encodeURIComponent(symbol || "")}`;
        const res = await fetch(url);
        const data = await res.json();
        if (active && data.success && data.data.length > 0) {
          setConstituents(data.data[0]);
        }
      } catch (err) {
        console.error(err);
      } finally {
        if (active) setLoading(false);
      }
    };
    fetchConstituent();
    return () => { active = false; };
  }, [runId, symbol, horizon]);

  if (loading) return <div className="empty-state">Loading stock analysis...</div>;
  if (!constituent) return <div className="empty-state">Analysis unavailable for {symbol}.</div>;

  const c = constituent;
  const tech = c.tech_details;
  const fund = c.fund_details;
  const relative_return = c.relative_return !== undefined ? c.relative_return : (c.company_return != null && c.benchmark_return != null ? c.company_return - c.benchmark_return : null);

  return (
    <div className="discovery-shell">
      <header className="page-header" style={{ display: 'flex', gap: '24px', alignItems: 'center' }}>
        <button onClick={() => navigate(-1)} className="secondary" style={{ padding: '8px 16px', height: '40px', background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>&larr; Back</button>
        <div>
          <p className="eyebrow">{c.sector} &rsaquo; {c.industry}</p>
          <h1 style={{ marginBottom: 0 }}>{c.symbol}</h1>
        </div>
      </header>

      <div className="dashboard-grid">
        {/* Technical Card */}
        <div className="panel run-card">
          <div className="run-card-header">
            <h3>Technical Details</h3>
            <span className={`badge ${c.technical_score != null && c.technical_score >= 70 ? "completed" : c.technical_score != null && c.technical_score < 40 ? "error" : "warning"}`}>
              Score: {c.technical_score != null ? c.technical_score.toFixed(1) : "-"}
            </span>
          </div>
          <ScoreBar score={c.technical_score} />
          <div className="run-card-content">
            <div className="run-card-section">
              <h4>Momentum Indicators</h4>
              <ul className="run-card-list">
                <li>
                  <span className="run-card-rank">CR</span> 
                  <span>Relative Return: <span className={relative_return >= 0 ? "score-high" : "score-low"}>{relative_return != null ? relative_return.toFixed(2) + '%' : '-'}</span></span>
                </li>
                <li>
                  <span className="run-card-rank">VC</span> 
                  <span style={{ display: 'flex', alignItems: 'center' }}>
                    Volume Score: <span style={{ marginLeft: '4px' }}>{tech?.technical_score?.components?.volume?.score?.toFixed(1) || '-'} pts</span>
                    <MiniBarChart score={tech?.technical_score?.components?.volume?.score} />
                  </span>
                </li>
                <li>
                  <span className="run-card-rank">CO</span> 
                  <span style={{ display: 'flex', alignItems: 'center' }}>
                    Consistency Score: <span style={{ marginLeft: '4px' }}>{tech?.consistency?.company_consistency_score?.toFixed(1) || '-'} pts</span>
                    <MiniBarChart score={tech?.consistency?.company_consistency_score} />
                  </span>
                </li>
              </ul>
              
              <ConsistencyChart periods={tech?.consistency?.periods} />
            </div>
          </div>
        </div>

        {/* Fundamental Card */}
        <div className="panel run-card">
          <div className="run-card-header">
            <h3>Fundamental Details</h3>
            <span className={`badge ${c.fundamental_score != null && c.fundamental_score >= 70 ? "completed" : c.fundamental_score != null && c.fundamental_score < 40 ? "error" : "warning"}`}>
              Score: {c.fundamental_score != null ? c.fundamental_score.toFixed(1) : "-"}
            </span>
          </div>
          <ScoreBar score={c.fundamental_score} />
          <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="run-card-section">
              <h4>1. Growth</h4>
              <ul className="run-card-list">
                <li><span className="run-card-rank">SG</span> <span>Sales Growth: <span className={(fund?.peer_benchmarks?.metrics?.sales_growth_pct?.company_value || 0) >= 0 ? "score-high" : "score-low"}>{fund?.peer_benchmarks?.metrics?.sales_growth_pct?.company_value != null ? fund.peer_benchmarks.metrics.sales_growth_pct.company_value.toFixed(2) + '%' : '-'}</span></span></li>
                <li><span className="run-card-rank">NG</span> <span>Net Profit Growth: <span className={(fund?.peer_benchmarks?.metrics?.net_profit_growth_pct?.company_value || 0) >= 0 ? "score-high" : "score-low"}>{fund?.peer_benchmarks?.metrics?.net_profit_growth_pct?.company_value != null ? fund.peer_benchmarks.metrics.net_profit_growth_pct.company_value.toFixed(2) + '%' : '-'}</span></span></li>
              </ul>
            </div>
            <div className="run-card-section">
              <h4>2. Profitability</h4>
              <ul className="run-card-list">
                <li><span className="run-card-rank">OM</span> <span>Op Margin: <span>{fund?.peer_benchmarks?.metrics?.latest_operating_margin_pct?.company_value != null ? fund.peer_benchmarks.metrics.latest_operating_margin_pct.company_value.toFixed(2) + '%' : '-'}</span></span></li>
                <li><span className="run-card-rank">MT</span> <span>Margin Trend: <span>{fund?.peer_benchmarks?.metrics?.operating_margin_change_pp?.company_value != null ? fund.peer_benchmarks.metrics.operating_margin_change_pp.company_value.toFixed(2) + ' pp' : '-'}</span></span></li>
              </ul>
            </div>
            <div className="run-card-section">
              <h4>3. Fin. Strength</h4>
              <ul className="run-card-list">
                <li><span className="run-card-rank">DE</span> <span>Debt/Equity: <span>{fund?.peer_benchmarks?.metrics?.debt_to_equity?.company_value != null ? fund.peer_benchmarks.metrics.debt_to_equity.company_value.toFixed(2) : '-'}</span></span></li>
                <li><span className="run-card-rank">BC</span> <span>Borrowing Change: <span>{fund?.peer_benchmarks?.metrics?.borrowing_change_pct?.company_value != null ? fund.peer_benchmarks.metrics.borrowing_change_pct.company_value.toFixed(2) + '%' : '-'}</span></span></li>
              </ul>
            </div>
            <div className="run-card-section">
              <h4>4. Earn Quality</h4>
              <ul className="run-card-list">
                <li><span className="run-card-rank">OC</span> <span>OCF to PAT: <span>{fund?.peer_benchmarks?.metrics?.latest_ocf_to_pat?.company_value != null ? fund.peer_benchmarks.metrics.latest_ocf_to_pat.company_value.toFixed(2) : '-'}</span></span></li>
                <li><span className="run-card-rank">PV</span> <span>Profit Volatility: <span>{fund?.peer_benchmarks?.metrics?.profit_volatility?.company_value != null ? fund.peer_benchmarks.metrics.profit_volatility.company_value.toFixed(2) : '-'}</span></span></li>
                <li><span className="run-card-rank">PH</span> <span>Profit History: <span>{fund?.peer_benchmarks?.metrics?.profit_history?.company_value != null ? fund.peer_benchmarks.metrics.profit_history.company_value.toFixed(2) + '%' : '-'}</span></span></li>
              </ul>
            </div>
          </div>
        </div>

        {/* Macro Card */}
        <div className="panel run-card">
          <div className="run-card-header">
            <h3>Macro Analysis</h3>
            <span className={`badge ${c.inherited_macro_score != null && c.inherited_macro_score >= 70 ? "completed" : c.inherited_macro_score != null && c.inherited_macro_score < 40 ? "error" : "warning"}`}>
              Score: {c.inherited_macro_score != null ? c.inherited_macro_score.toFixed(1) : "-"}
            </span>
          </div>
          <ScoreBar score={c.inherited_macro_score} />
          <div className="run-card-content">
            <div className="run-card-section">
              <h4>Economic Indicators</h4>
              <ul className="run-card-list">
                {c.macro_impact && c.macro_impact.category_impacts ? (
                  Object.entries(c.macro_impact.category_impacts).map(([key, details]: [string, any]) => (
                    <li key={key}>
                      <span className="run-card-rank">{key.substring(0, 2).toUpperCase()}</span>
                      <span>
                        {key.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, l => l.toUpperCase())}:{' '}
                        <span className={details.impact === 'POSITIVE' ? 'score-high' : details.impact === 'NEGATIVE' ? 'score-low' : ''}>
                          {details.impact} ({details.confidence?.toLowerCase()})
                        </span>
                      </span>
                    </li>
                  ))
                ) : (
                  <li><span className="run-card-rank">IN</span> <span>Inherited From: <span>{c.sector} / {c.industry}</span></span></li>
                )}
              </ul>
            </div>
            
            {c.macro_impact && (c.macro_impact.reason || c.macro_impact.overall_impact) && (
              <div className="run-card-section" style={{ marginTop: '16px' }}>
                <h4>Overall Outlook</h4>
                <div style={{ padding: '12px', background: 'var(--bg-color)', borderRadius: '8px', border: '1px solid var(--panel-border)', fontSize: '0.9rem', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
                  <div style={{ marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span className={`badge ${c.macro_impact.overall_impact?.impact === 'POSITIVE' ? 'completed' : c.macro_impact.overall_impact?.impact === 'NEGATIVE' ? 'error' : 'warning'}`}>
                      {c.macro_impact.overall_impact?.impact || "N/A"}
                    </span>
                    <strong>{c.sector}</strong>
                  </div>
                  {c.macro_impact.reason || c.macro_impact.overall_impact?.reason}
                </div>
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
