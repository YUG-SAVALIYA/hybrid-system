import { useEffect, useState } from "react";
import { StockViewParams } from "../App";

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

export function StockDetailsPage({
  runId,
  stock,
  onBack
}: {
  runId: string;
  stock: StockViewParams;
  onBack: () => void;
}) {
  const [constituent, setConstituents] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const fetchConstituent = async () => {
      setLoading(true);
      try {
        const url = `/api/v1/discovery/runs/${runId}/constituents?horizon=${stock.horizon}&entity_type=STOCK&entity_name=${encodeURIComponent(stock.symbol)}`;
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
  }, [runId, stock]);

  if (loading) return <div className="empty-state">Loading stock analysis...</div>;
  if (!constituent) return <div className="empty-state">Analysis unavailable for {stock.symbol}.</div>;

  const c = constituent;
  const tech = c.tech_details;
  const fund = c.fund_details;

  return (
    <div className="discovery-shell">
      <header className="dashboard-hero" style={{ gridTemplateColumns: 'none', display: 'flex', gap: '20px', alignItems: 'center' }}>
        <button onClick={onBack} className="secondary" style={{ padding: '8px 18px', height: '40px', flexShrink: 0 }}>&larr; Back</button>
        <div className="hero-copy">
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
              <h4>Returns</h4>
              <ul className="run-card-list">
                <li><span className="run-card-rank">CR</span> <span>Company Return: <span className={c.company_return >= 0 ? "score-high" : "score-low"}>{c.company_return != null ? c.company_return.toFixed(2) + '%' : '-'}</span></span></li>
                <li><span className="run-card-rank">BR</span> <span>Benchmark Return: <span>{c.benchmark_return != null ? c.benchmark_return.toFixed(2) + '%' : '-'}</span></span></li>
              </ul>
            </div>
            <div className="run-card-section">
              <h4>Consistency</h4>
              <ul className="run-card-list">
                <li><span className="run-card-rank">VC</span> <span>Vol Change: <span>{tech?.technical_score?.components?.volume?.score?.toFixed(1) || '-'} pts</span></span></li>
                <li><span className="run-card-rank">CS</span> <span>Consistency: <span>{tech?.technical_score?.components?.consistency?.score?.toFixed(1) || '-'} pts</span></span></li>
                <li><span className="run-card-rank">PV</span> <span>Pos/Valid: <span>{tech?.consistency?.positive_periods ?? '-'}/{tech?.consistency?.valid_periods ?? '-'}</span></span></li>
              </ul>
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
          <div className="run-card-content">
            <div className="run-card-section">
              <h4>Growth & Margin</h4>
              <ul className="run-card-list">
                <li><span className="run-card-rank">SG</span> <span>Sales Growth: <span className={(fund?.peer_benchmarks?.metrics?.sales_growth_pct?.company_value || 0) >= 0 ? "score-high" : "score-low"}>{fund?.peer_benchmarks?.metrics?.sales_growth_pct?.company_value != null ? fund.peer_benchmarks.metrics.sales_growth_pct.company_value.toFixed(2) + '%' : '-'}</span></span></li>
                <li><span className="run-card-rank">OM</span> <span>Op Margin: <span>{fund?.peer_benchmarks?.metrics?.latest_operating_margin_pct?.company_value != null ? fund.peer_benchmarks.metrics.latest_operating_margin_pct.company_value.toFixed(2) + '%' : '-'}</span></span></li>
                <li><span className="run-card-rank">MT</span> <span>Margin Trend: <span>{fund?.peer_benchmarks?.metrics?.operating_margin_change_pp?.company_value != null ? fund.peer_benchmarks.metrics.operating_margin_change_pp.company_value.toFixed(2) + ' pp' : '-'}</span></span></li>
              </ul>
            </div>
            <div className="run-card-section">
              <h4>Strength & Quality</h4>
              <ul className="run-card-list">
                <li><span className="run-card-rank">DE</span> <span>Debt/Equity: <span>{fund?.peer_benchmarks?.metrics?.debt_to_equity?.company_value != null ? fund.peer_benchmarks.metrics.debt_to_equity.company_value.toFixed(2) : '-'}</span></span></li>
                <li><span className="run-card-rank">BC</span> <span>Borrowing Change: <span>{fund?.peer_benchmarks?.metrics?.borrowing_change_pct?.company_value != null ? fund.peer_benchmarks.metrics.borrowing_change_pct.company_value.toFixed(2) + '%' : '-'}</span></span></li>
                <li><span className="run-card-rank">OC</span> <span>OCF to PAT: <span>{fund?.peer_benchmarks?.metrics?.latest_ocf_to_pat?.company_value != null ? fund.peer_benchmarks.metrics.latest_ocf_to_pat.company_value.toFixed(2) : '-'}</span></span></li>
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
