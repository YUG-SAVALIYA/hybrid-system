import { useEffect, useState } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, ReferenceLine } from "recharts";
import { ScoreCell, ScoreExplanationBanner } from "../components/ExplanationBanner";

function ScoreBar({ score }: { score: number | null }) {
  if (score === null || isNaN(score)) return <div style={{ background: '#27272a', height: '6px', borderRadius: '3px', width: '100%', marginBottom: '16px' }} />;
  const width = `${Math.min(Math.max(score, 0), 100)}%`;
  const color = score >= 75 ? '#10b981' : score < 50 ? '#f43f5e' : '#f59e0b';
  
  return (
    <div style={{ background: '#27272a', height: '6px', borderRadius: '3px', width: '100%', overflow: 'hidden', marginBottom: '16px' }}>
      <div style={{ width, background: color, height: '100%', transition: 'width 0.5s ease-out' }} />
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
    <div style={{ height: 280, width: '100%', marginTop: '24px', background: "#18181b", padding: "16px", borderRadius: "12px", border: "1px solid #27272a" }}>
      <h4 style={{ marginBottom: '12px', color: "#ffffff" }}>📊 5-Period Stock Return vs Benchmark</h4>
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
          <Bar dataKey="company" name="Company Stock Return" fill="#ffffff" radius={[4, 4, 0, 0]} />
          <Bar dataKey="benchmark" name="Benchmark Return" fill="#71717a" radius={[4, 4, 0, 0]} />
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

  if (loading) return <div className="empty-state">Loading stock analysis for {symbol}...</div>;
  if (!constituent) return <div className="empty-state">Detailed analysis unavailable for {symbol}.</div>;

  const c = constituent;
  const tech = c.tech_details;
  const fund = c.fund_details;
  const relative_return = c.relative_return !== undefined ? c.relative_return : (c.company_return != null && c.benchmark_return != null ? c.company_return - c.benchmark_return : null);

  return (
    <div className="discovery-shell">
      <header className="page-header" style={{ display: 'flex', gap: '20px', alignItems: 'center' }}>
        <button onClick={() => navigate(-1)} className="secondary" style={{ padding: '8px 18px', height: '40px', flexShrink: 0 }}>&larr; Back to Results</button>
        <div>
          <p className="eyebrow" style={{ color: "var(--text-muted)", fontSize: "0.8rem", textTransform: "uppercase" }}>
            {c.sector} &rsaquo; {c.industry}
          </p>
          <h1 style={{ margin: "2px 0 0 0" }}>{c.symbol} Stock Deep Dive</h1>
        </div>
      </header>

      <ScoreExplanationBanner />

      <div className="dashboard-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))" }}>
        {/* Technical Card */}
        <div className="panel run-card">
          <div className="run-card-header">
            <div>
              <h3>Technical Momentum & Trend</h3>
              <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", marginTop: "2px" }}>
                Price relative return, volume profile & 5-period trend consistency.
              </div>
            </div>
            <ScoreCell score={c.technical_score} />
          </div>
          <ScoreBar score={c.technical_score} />

          <div className="run-card-content">
            <div className="run-card-section">
              <h4>Momentum Indicators</h4>
              <ul className="run-card-list">
                <li>
                  <span>Relative Return vs Benchmark:</span>
                  <strong style={{ color: relative_return >= 0 ? "#10b981" : "#f43f5e" }}>
                    {relative_return != null ? (relative_return >= 0 ? `+${relative_return.toFixed(2)}%` : `${relative_return.toFixed(2)}%`) : '-'}
                  </strong>
                </li>
                <li>
                  <span>Volume & Accumulation Score:</span>
                  <strong>{tech?.technical_score?.components?.volume?.score ? `${tech.technical_score.components.volume.score.toFixed(1)} / 100` : '-'}</strong>
                </li>
                <li>
                  <span>Consistency Score:</span>
                  <strong>{tech?.consistency?.company_consistency_score ? `${tech.consistency.company_consistency_score.toFixed(1)} / 100` : '-'}</strong>
                </li>
              </ul>
              
              <ConsistencyChart periods={tech?.consistency?.periods} />
            </div>
          </div>
        </div>

        {/* Fundamental Card */}
        <div className="panel run-card">
          <div className="run-card-header">
            <div>
              <h3>Fundamental Financial Ratios</h3>
              <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", marginTop: "2px" }}>
                Sales growth, operating margins, leverage safety & cash flow quality.
              </div>
            </div>
            <ScoreCell score={c.fundamental_score} />
          </div>
          <ScoreBar score={c.fundamental_score} />

          <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
            <div className="run-card-section">
              <h4>1. Revenue & Earnings Growth</h4>
              <ul className="run-card-list">
                <li>Sales Growth: <strong>{fund?.peer_benchmarks?.metrics?.sales_growth_pct?.company_value != null ? fund.peer_benchmarks.metrics.sales_growth_pct.company_value.toFixed(2) + '%' : '-'}</strong></li>
                <li>Net Profit Growth: <strong>{fund?.peer_benchmarks?.metrics?.net_profit_growth_pct?.company_value != null ? fund.peer_benchmarks.metrics.net_profit_growth_pct.company_value.toFixed(2) + '%' : '-'}</strong></li>
              </ul>
            </div>

            <div className="run-card-section">
              <h4>2. Profit Margins</h4>
              <ul className="run-card-list">
                <li>Operating Margin: <strong>{fund?.peer_benchmarks?.metrics?.latest_operating_margin_pct?.company_value != null ? fund.peer_benchmarks.metrics.latest_operating_margin_pct.company_value.toFixed(2) + '%' : '-'}</strong></li>
                <li>Margin Expansion: <strong>{fund?.peer_benchmarks?.metrics?.operating_margin_change_pp?.company_value != null ? fund.peer_benchmarks.metrics.operating_margin_change_pp.company_value.toFixed(2) + ' pp' : '-'}</strong></li>
              </ul>
            </div>

            <div className="run-card-section">
              <h4>3. Balance Sheet Safety</h4>
              <ul className="run-card-list">
                <li>Debt-to-Equity: <strong>{fund?.peer_benchmarks?.metrics?.debt_to_equity?.company_value != null ? fund.peer_benchmarks.metrics.debt_to_equity.company_value.toFixed(2) : '-'}</strong></li>
                <li>Borrowing Change: <strong>{fund?.peer_benchmarks?.metrics?.borrowing_change_pct?.company_value != null ? fund.peer_benchmarks.metrics.borrowing_change_pct.company_value.toFixed(2) + '%' : '-'}</strong></li>
              </ul>
            </div>

            <div className="run-card-section">
              <h4>4. Cash Flow & Quality</h4>
              <ul className="run-card-list">
                <li>OCF to PAT Ratio: <strong>{fund?.peer_benchmarks?.metrics?.latest_ocf_to_pat?.company_value != null ? fund.peer_benchmarks.metrics.latest_ocf_to_pat.company_value.toFixed(2) : '-'}</strong></li>
                <li>Profitable History: <strong>{fund?.peer_benchmarks?.metrics?.profit_history?.company_value != null ? fund.peer_benchmarks.metrics.profit_history.company_value.toFixed(2) + '%' : '-'}</strong></li>
              </ul>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
