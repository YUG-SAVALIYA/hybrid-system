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

function getFundPillarScore(group: any, fundObj: any, pillarName: string): number | null {
  const p1 = fundObj?.pillar_scores?.[pillarName]?.score;
  if (p1 != null && !isNaN(p1)) return Number(p1);
  
  const groupColKey = `fundamental_${pillarName}_score`;
  if (group?.[groupColKey] != null && !isNaN(group[groupColKey])) return Number(group[groupColKey]);
  
  if (!fundObj) return null;
  const pDirect = fundObj?.[pillarName]?.score;
  if (pDirect != null && !isNaN(pDirect)) return Number(pDirect);
  const p2 = fundObj?.fundamental_scoring?.final?.components?.[pillarName]?.score;
  if (p2 != null && !isNaN(p2)) return Number(p2);
  const p3 = fundObj?.fundamental_scoring?.[pillarName]?.score;
  if (p3 != null && !isNaN(p3)) return Number(p3);
  const p4 = fundObj?.components?.[pillarName]?.score;
  if (p4 != null && !isNaN(p4)) return Number(p4);
  return null;
}

function getTechSubScore(group: any, techObj: any, key: string): number | null {
  if (techObj?.scores) {
    const sSub = techObj.scores[key] ?? techObj.scores[`${key}_score`];
    if (sSub != null && !isNaN(sSub)) return Number(sSub);
  }
  const groupColKey = `technical_${key}_score`;
  if (group?.[groupColKey] != null && !isNaN(group[groupColKey])) return Number(group[groupColKey]);
  
  if (!techObj) return null;
  const sDirect = techObj?.[key]?.score ?? techObj?.[key]?.percentile_rank ?? techObj?.[`${key}_score`];
  if (sDirect != null && !isNaN(sDirect)) return Number(sDirect);
  const s1 = techObj?.components?.[key]?.score;
  if (s1 != null && !isNaN(s1)) return Number(s1);
  const s2 = techObj?.technical_score?.components?.[key]?.score;
  if (s2 != null && !isNaN(s2)) return Number(s2);
  const s4 = techObj?.[`${key}`];
  if (typeof s4 === 'number' && !isNaN(s4)) return Number(s4);
  return null;
}

function MetricTile({ 
  label, 
  value, 
  subtext, 
  color, 
  tooltip 
}: { 
  label: string; 
  value: string | number; 
  subtext?: string; 
  color?: string; 
  tooltip?: string; 
}) {
  return (
    <div className="metric-tile">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="metric-tile-label">{label}</div>
        {tooltip && (
          <span 
            title={tooltip} 
            style={{ 
              cursor: "pointer", 
              fontSize: "0.7rem", 
              color: "#a1a1aa", 
              background: "#27272a", 
              borderRadius: "50%", 
              width: "15px", 
              height: "15px", 
              display: "inline-flex", 
              alignItems: "center", 
              justifyContent: "center",
              fontWeight: 700 
            }}
          >
            ?
          </span>
        )}
      </div>
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
          <Bar dataKey="company" name="Median Group Stock Return" fill="#ffffff" radius={[4, 4, 0, 0]} />
          <Bar dataKey="benchmark" name="Median Benchmark Return" fill="#71717a" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function GroupDetailsPage() {
  const { runId, entityType, name } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const horizon = (searchParams.get("horizon") || "SHORT") as DiscoveryHorizon;
  const type = entityType?.toUpperCase() || "SECTOR";
  const parentSector = searchParams.get("parent_sector") || "";
  const parentIndustry = searchParams.get("parent_industry") || "";

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

  const tech = groupDetails?.tech_details || (groupDetails as any)?.calculation_details?.technical;
  const fund = groupDetails?.fund_details || (groupDetails as any)?.calculation_details?.fundamental;
  const macro = groupDetails?.macro_details || (groupDetails as any)?.calculation_details?.macro;

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

  const horizonLabel = horizon === "LONG" ? "1 Month (1M)" : horizon === "MID" ? "1 Week (1W)" : "1 Day (1D)";

  const growthScore = getFundPillarScore(groupDetails, fund, "growth");
  const profScore = getFundPillarScore(groupDetails, fund, "profitability");
  const fsScore = getFundPillarScore(groupDetails, fund, "financial_strength");
  const eqScore = getFundPillarScore(groupDetails, fund, "earnings_quality");

  const techReturnScore = getTechSubScore(groupDetails, tech, "return");
  const techBreadthScore = getTechSubScore(groupDetails, tech, "breadth");
  const techVolumeScore = getTechSubScore(groupDetails, tech, "volume");
  const techConsistencyScore = getTechSubScore(groupDetails, tech, "consistency");

  const rawMetrics = fund?.raw_aggregation?.metrics || fund?.metrics || {};

  const filteredConstituents = constituents.filter((c) =>
    c.symbol.toLowerCase().includes(filter.toLowerCase()) ||
    (c.sector && c.sector.toLowerCase().includes(filter.toLowerCase())) ||
    (c.industry && c.industry.toLowerCase().includes(filter.toLowerCase()))
  );

  return (
    <div className="discovery-shell">
      <header className="dashboard-hero" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '20px' }}>
        <div>
          <button onClick={() => navigate(-1)} className="secondary" style={{ padding: '6px 14px', height: '32px', fontSize: '0.82rem', marginBottom: '10px' }}>
            &larr; Back to Results
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

      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
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

          <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '20px' }}>
            {/* Pillar 1: Relative Return */}
            <div className="run-card-section" style={{ background: "#121215", padding: "14px", borderRadius: "8px", border: "1px solid #27272a" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #27272a", paddingBottom: "6px" }}>
                <h4 style={{ margin: 0, fontSize: "0.88rem", color: "#ffffff" }}>📈 1. Relative Return Pillar</h4>
                {techReturnScore != null ? (
                  <span className={`badge ${techReturnScore >= 50 ? 'score-high' : 'score-low'}`} style={{ fontSize: "0.72rem", padding: "2px 7px" }}>
                    {techReturnScore.toFixed(1)} / 100
                  </span>
                ) : (
                  <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 7px", opacity: 0.7 }}>N/A</span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <MetricTile 
                  label="Relative Return (Median)" 
                  value={tech?.median_relative_return != null ? `${tech.median_relative_return >= 0 ? '+' : ''}${tech.median_relative_return.toFixed(1)}%` : 'N/A'}
                  subtext="Outperformance vs Benchmark"
                  color={tech?.median_relative_return >= 0 ? "#10b981" : "#f43f5e"}
                  tooltip="Median return of sector stocks minus benchmark index return over horizon period."
                />
              </div>
            </div>

            {/* Pillar 2: Market Breadth */}
            <div className="run-card-section" style={{ background: "#121215", padding: "14px", borderRadius: "8px", border: "1px solid #27272a" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #27272a", paddingBottom: "6px" }}>
                <h4 style={{ margin: 0, fontSize: "0.88rem", color: "#ffffff" }}>📊 2. Market Breadth Pillar</h4>
                {techBreadthScore != null ? (
                  <span className={`badge ${techBreadthScore >= 50 ? 'score-high' : 'score-low'}`} style={{ fontSize: "0.72rem", padding: "2px 7px" }}>
                    {techBreadthScore.toFixed(1)} / 100
                  </span>
                ) : (
                  <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 7px", opacity: 0.7 }}>N/A</span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <MetricTile 
                  label="Positive Return Ratio" 
                  value={tech?.positive_return_breadth != null ? `${tech.positive_return_breadth.toFixed(1)}%` : 'N/A'}
                  subtext="% constituents with positive gain"
                  tooltip="% of member stocks in the sector that generated a positive return."
                />
                <MetricTile 
                  label="Outperformance Ratio" 
                  value={tech?.outperformance_breadth != null ? `${tech.outperformance_breadth.toFixed(1)}%` : 'N/A'}
                  subtext="% constituents beating index"
                  tooltip="% of member stocks that generated higher return than reference benchmark."
                />
              </div>
            </div>

            {/* Pillar 3: Volume */}
            <div className="run-card-section" style={{ background: "#121215", padding: "14px", borderRadius: "8px", border: "1px solid #27272a" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #27272a", paddingBottom: "6px" }}>
                <h4 style={{ margin: 0, fontSize: "0.88rem", color: "#ffffff" }}>🏦 3. Volume & Demand Pillar</h4>
                {techVolumeScore != null ? (
                  <span className={`badge ${techVolumeScore >= 50 ? 'score-high' : 'score-low'}`} style={{ fontSize: "0.72rem", padding: "2px 7px" }}>
                    {techVolumeScore.toFixed(1)} / 100
                  </span>
                ) : (
                  <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 7px", opacity: 0.7 }}>N/A</span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <MetricTile 
                  label="Volume Accumulation" 
                  value={techVolumeScore != null ? `${techVolumeScore.toFixed(1)} / 100` : 'N/A'}
                  subtext="Institutional Buying Index"
                  tooltip="Measures institutional accumulation by comparing trading volume on up days vs down days."
                />
              </div>
            </div>

            {/* Pillar 4: Momentum & Consistency */}
            <div className="run-card-section" style={{ background: "#121215", padding: "14px", borderRadius: "8px", border: "1px solid #27272a" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #27272a", paddingBottom: "6px" }}>
                <h4 style={{ margin: 0, fontSize: "0.88rem", color: "#ffffff" }}>🔄 4. Momentum Consistency Pillar</h4>
                {techConsistencyScore != null ? (
                  <span className={`badge ${techConsistencyScore >= 50 ? 'score-high' : 'score-low'}`} style={{ fontSize: "0.72rem", padding: "2px 7px" }}>
                    {techConsistencyScore.toFixed(1)} / 100
                  </span>
                ) : (
                  <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 7px", opacity: 0.7 }}>N/A</span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <MetricTile 
                  label="High Consistency Rate" 
                  value={tech?.percent_consistency_gte_60 != null ? `${tech.percent_consistency_gte_60.toFixed(1)}%` : 'N/A'}
                  subtext="% stocks with ≥60% consistency"
                  tooltip="% of member stocks showing consistent positive momentum across 5 historical sub-periods."
                />
              </div>
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
                Sales growth, operating margins, leverage safety & earnings stability across member stocks.
              </div>
            </div>
            <ScoreCell score={groupDetails.fundamental_score} />
          </div>
          <ScoreBar score={groupDetails.fundamental_score} />

          <div className="run-card-content" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '20px' }}>
            {/* Pillar 1: Growth */}
            <div className="run-card-section" style={{ background: "#121215", padding: "14px", borderRadius: "8px", border: "1px solid #27272a" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #27272a", paddingBottom: "6px" }}>
                <h4 style={{ margin: 0, fontSize: "0.88rem", color: "#ffffff" }}>🌱 1. Growth Pillar</h4>
                {growthScore != null ? (
                  <span className={`badge ${growthScore >= 50 ? 'score-high' : 'score-low'}`} style={{ fontSize: "0.72rem", padding: "2px 7px" }}>
                    {growthScore.toFixed(1)} / 100
                  </span>
                ) : (
                  <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 7px", opacity: 0.7 }}>N/A (Unavailable)</span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <MetricTile 
                  label="Sales Growth (Median)" 
                  value={(rawMetrics?.sales_growth_pct?.median ?? rawMetrics?.sales_growth_pct?.raw_median) != null ? `${(rawMetrics.sales_growth_pct.median ?? rawMetrics.sales_growth_pct.raw_median).toFixed(1)}%` : 'N/A'} 
                  subtext={(rawMetrics?.sales_growth_pct?.median ?? rawMetrics?.sales_growth_pct?.raw_median) != null ? "YoY revenue growth" : "No YoY sales data in filings"} 
                  tooltip="Year-over-Year percentage change in sales revenue across sector constituents."
                />
                <MetricTile 
                  label="Net Profit Growth" 
                  value={(rawMetrics?.net_profit_growth_pct?.median ?? rawMetrics?.net_profit_growth_pct?.raw_median) != null 
                    ? `${(rawMetrics.net_profit_growth_pct.median ?? rawMetrics.net_profit_growth_pct.raw_median).toFixed(1)}%` 
                    : growthScore != null ? "Turnaround / Transition" : "N/A"} 
                  subtext={(rawMetrics?.net_profit_growth_pct?.median ?? rawMetrics?.net_profit_growth_pct?.raw_median) != null 
                    ? "Bottom-line earnings" 
                    : growthScore != null ? "Scored via profit status shifts" : "Filing data pending"} 
                  tooltip="YoY percentage change in net profit, or scored via structural loss-to-profit transition shifts."
                />
              </div>
            </div>

            {/* Pillar 2: Profitability */}
            <div className="run-card-section" style={{ background: "#121215", padding: "14px", borderRadius: "8px", border: "1px solid #27272a" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #27272a", paddingBottom: "6px" }}>
                <h4 style={{ margin: 0, fontSize: "0.88rem", color: "#ffffff" }}>💰 2. Profitability Pillar</h4>
                {profScore != null ? (
                  <span className={`badge ${profScore >= 50 ? 'score-high' : 'score-low'}`} style={{ fontSize: "0.72rem", padding: "2px 7px" }}>
                    {profScore.toFixed(1)} / 100
                  </span>
                ) : (
                  <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 7px", opacity: 0.7 }}>N/A (Unavailable)</span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <MetricTile 
                  label="Operating Margin" 
                  value={(rawMetrics?.latest_operating_margin_pct?.median ?? rawMetrics?.latest_operating_margin_pct?.raw_median) != null ? `${(rawMetrics.latest_operating_margin_pct.median ?? rawMetrics.latest_operating_margin_pct.raw_median).toFixed(1)}%` : 'N/A'} 
                  subtext={(rawMetrics?.latest_operating_margin_pct?.median ?? rawMetrics?.latest_operating_margin_pct?.raw_median) != null ? "Operating efficiency" : "Requires operating filings"} 
                  tooltip="Operating Income divided by Net Sales (EBIT margin %). High margin indicates pricing power."
                />
                <MetricTile 
                  label="Margin Expansion" 
                  value={(rawMetrics?.operating_margin_change_pp?.median ?? rawMetrics?.operating_margin_change_pp?.raw_median) != null ? `${(rawMetrics.operating_margin_change_pp.median ?? rawMetrics.operating_margin_change_pp.raw_median).toFixed(1)} pp` : 'N/A'} 
                  subtext={(rawMetrics?.operating_margin_change_pp?.median ?? rawMetrics?.operating_margin_change_pp?.raw_median) != null ? "Margin trend" : "Filing data pending"} 
                  tooltip="Percentage point change in operating margin vs previous period. Positive means expanding margins."
                />
              </div>
            </div>

            {/* Pillar 3: Financial Strength */}
            <div className="run-card-section" style={{ background: "#121215", padding: "14px", borderRadius: "8px", border: "1px solid #27272a" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #27272a", paddingBottom: "6px" }}>
                <h4 style={{ margin: 0, fontSize: "0.88rem", color: "#ffffff" }}>🛡️ 3. Financial Strength Pillar</h4>
                {fsScore != null ? (
                  <span className={`badge ${fsScore >= 50 ? 'score-high' : 'score-low'}`} style={{ fontSize: "0.72rem", padding: "2px 7px" }}>
                    {fsScore.toFixed(1)} / 100
                  </span>
                ) : (
                  <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 7px", opacity: 0.7 }}>N/A (Unavailable)</span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <MetricTile 
                  label="Debt-to-Equity (Median)" 
                  value={(rawMetrics?.debt_to_equity?.median ?? rawMetrics?.debt_to_equity?.raw_median) != null ? (rawMetrics.debt_to_equity.median ?? rawMetrics.debt_to_equity.raw_median).toFixed(2) : 'N/A'} 
                  subtext="Financial leverage" 
                  tooltip="Total Debt divided by Equity. Lower value (<0.5) indicates strong balance sheet and debt safety."
                />
                <MetricTile 
                  label="Borrowing Change" 
                  value={(rawMetrics?.borrowing_change_pct?.median ?? rawMetrics?.borrowing_change_pct?.raw_median) != null ? `${(rawMetrics.borrowing_change_pct.median ?? rawMetrics.borrowing_change_pct.raw_median).toFixed(1)}%` : 'N/A'} 
                  subtext="Debt trend" 
                  tooltip="Percentage change in borrowing liabilities. Negative change means debt repayment/deleveraging."
                />
              </div>
            </div>

            {/* Pillar 4: Earnings Quality */}
            <div className="run-card-section" style={{ background: "#121215", padding: "14px", borderRadius: "8px", border: "1px solid #27272a" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", borderBottom: "1px solid #27272a", paddingBottom: "6px" }}>
                <h4 style={{ margin: 0, fontSize: "0.88rem", color: "#ffffff" }}>💵 4. Earnings Quality Pillar</h4>
                {eqScore != null ? (
                  <span className={`badge ${eqScore >= 50 ? 'score-high' : 'score-low'}`} style={{ fontSize: "0.72rem", padding: "2px 7px" }}>
                    {eqScore.toFixed(1)} / 100
                  </span>
                ) : (
                  <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 7px", opacity: 0.7 }}>N/A (Unavailable)</span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <MetricTile 
                  label="Profitable History" 
                  value={(rawMetrics?.positive_pat_period_ratio?.median ?? rawMetrics?.positive_pat_period_ratio?.raw_median) != null ? `${(rawMetrics.positive_pat_period_ratio.median ?? rawMetrics.positive_pat_period_ratio.raw_median).toFixed(1)}%` : 'N/A'} 
                  subtext="% periods profitable" 
                  tooltip="Percentage of past financial periods with positive net profit. 100% means consistent profitability."
                />
                <MetricTile 
                  label="PAT Volatility Index" 
                  value={(rawMetrics?.pat_growth_volatility_pct?.median ?? rawMetrics?.pat_growth_volatility_pct?.raw_median) != null ? `${(rawMetrics.pat_growth_volatility_pct.median ?? rawMetrics.pat_growth_volatility_pct.raw_median).toFixed(1)}%` : 'N/A'} 
                  subtext="Earnings stability" 
                  tooltip="Standard deviation of profit growth over time. Lower value (<40%) means stable predictable earnings."
                />
                <MetricTile 
                  label="OCF to PAT Ratio" 
                  value={(rawMetrics?.latest_ocf_to_pat?.median ?? rawMetrics?.latest_ocf_to_pat?.raw_median) != null ? (rawMetrics.latest_ocf_to_pat.median ?? rawMetrics.latest_ocf_to_pat.raw_median).toFixed(2) : 'N/A'} 
                  subtext={(rawMetrics?.latest_ocf_to_pat?.median ?? rawMetrics?.latest_ocf_to_pat?.raw_median) != null ? "Cash flow conversion" : "Filing data pending"} 
                  tooltip="Operating Cash Flow divided by Net Profit. Ratio ≥ 1.0 proves profits are backed by actual cash inflows."
                />
              </div>
            </div>
          </div>

          {/* Pillar Scores Bar */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", marginTop: "16px", paddingTop: "14px", borderTop: "1px solid var(--panel-border)" }}>
            {growthScore != null && (
              <div className="pillar-chip">
                <span style={{ color: "var(--text-muted)" }}>Growth Pillar:</span>
                <span style={{ color: growthScore >= 50 ? "#10b981" : "#f43f5e", fontWeight: 700, marginLeft: "4px" }}>{growthScore.toFixed(1)} / 100</span>
              </div>
            )}
            {profScore != null && (
              <div className="pillar-chip">
                <span style={{ color: "var(--text-muted)" }}>Profitability Pillar:</span>
                <span style={{ color: profScore >= 50 ? "#10b981" : "#f43f5e", fontWeight: 700, marginLeft: "4px" }}>{profScore.toFixed(1)} / 100</span>
              </div>
            )}
            {fsScore != null && (
              <div className="pillar-chip">
                <span style={{ color: "var(--text-muted)" }}>Financial Strength:</span>
                <span style={{ color: fsScore >= 50 ? "#10b981" : "#f43f5e", fontWeight: 700, marginLeft: "4px" }}>{fsScore.toFixed(1)} / 100</span>
              </div>
            )}
            {eqScore != null && (
              <div className="pillar-chip">
                <span style={{ color: "var(--text-muted)" }}>Earnings Quality:</span>
                <span style={{ color: eqScore >= 50 ? "#10b981" : "#f43f5e", fontWeight: 700, marginLeft: "4px" }}>{eqScore.toFixed(1)} / 100</span>
              </div>
            )}
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
