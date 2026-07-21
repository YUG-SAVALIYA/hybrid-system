import { useEffect, useState } from "react";
import { GroupViewParams } from "../App";

export function GroupDetailsPage({
  runId,
  group,
  onBack,
  onStockSelect
}: {
  runId: string;
  group: GroupViewParams;
  onBack: () => void;
  onStockSelect: (stock: {symbol: string, horizon: string}) => void;
}) {
  const [constituents, setConstituents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const fetchConstituents = async () => {
      setLoading(true);
      try {
        const url = `/api/v1/discovery/runs/${runId}/constituents?horizon=${group.horizon}&entity_type=${group.type}&entity_name=${encodeURIComponent(group.name)}&parent_sector=${encodeURIComponent(group.parentSector)}&parent_industry=${encodeURIComponent(group.parentIndustry)}`;
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
  }, [runId, group]);

  return (
    <div className="discovery-shell">
      <header className="dashboard-hero" style={{ gridTemplateColumns: 'none', display: 'flex', gap: '20px', alignItems: 'center' }}>
        <button onClick={onBack} className="secondary" style={{ padding: '8px 18px', height: '40px', flexShrink: 0 }}>&larr; Back</button>
        <div className="hero-copy">
          <p className="eyebrow">{group.type.replace('_', ' ')} CONSTITUENTS</p>
          <h1 style={{ marginBottom: 0 }}>{group.name}</h1>
        </div>
      </header>
      
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
                    onClick={() => onStockSelect({ symbol: c.symbol, horizon: group.horizon })} 
                    style={{ cursor: 'pointer' }} 
                    className="clickable-row"
                  >
                    <td style={{ fontWeight: 600, color: 'var(--accent-primary)' }}>{c.symbol}</td>
                    <td>{c.sector}</td>
                    <td>{c.industry}</td>
                    <td className={c.technical_score != null && c.technical_score >= 70 ? "score-high" : c.technical_score != null && c.technical_score < 40 ? "score-low" : "score-mid"}>
                      {c.technical_score != null ? c.technical_score.toFixed(1) : "-"}
                    </td>
                    <td className={c.fundamental_score != null && c.fundamental_score >= 70 ? "score-high" : c.fundamental_score != null && c.fundamental_score < 40 ? "score-low" : "score-mid"}>
                      {c.fundamental_score != null ? c.fundamental_score.toFixed(1) : "-"}
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
