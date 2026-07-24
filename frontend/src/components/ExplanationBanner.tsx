import { useState } from "react";

export function ScoreExplanationBanner() {
  const [open, setOpen] = useState(true);

  if (!open) {
    return (
      <button 
        className="secondary" 
        onClick={() => setOpen(true)}
        style={{ fontSize: "0.8rem", padding: "6px 12px", minHeight: "30px", marginBottom: "16px" }}
      >
        💡 How to understand these scores?
      </button>
    );
  }

  return (
    <div className="info-banner">
      <div className="info-banner-title">
        <span>💡 How to Understand the Metrics & Scores</span>
        <button 
          onClick={() => setOpen(false)}
          style={{ marginLeft: "auto", background: "transparent", border: "none", color: "var(--text-muted)", minHeight: "auto", padding: "2px 8px", cursor: "pointer" }}
          title="Hide guide"
        >
          ✕
        </button>
      </div>
      <div className="info-banner-desc" style={{ marginBottom: "12px" }}>
        Every sector, industry, and stock is evaluated using an automated multi-factor scoring model rated from <strong>0 to 100</strong>:
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "8px" }}>
          <span className="badge score-high" style={{ background: "rgba(16, 185, 129, 0.2)", color: "#10b981", border: "1px solid #10b981" }}>80–100: Very Strong</span>
          <span className="badge score-high" style={{ background: "rgba(52, 211, 153, 0.2)", color: "#34d399", border: "1px solid #34d399" }}>65–79: Strong</span>
          <span className="badge score-mid" style={{ background: "rgba(245, 158, 11, 0.2)", color: "#f59e0b", border: "1px solid #f59e0b" }}>50–64: Neutral</span>
          <span className="badge score-low" style={{ background: "rgba(251, 113, 133, 0.2)", color: "#fb7185", border: "1px solid #fb7185" }}>35–49: Weak</span>
          <span className="badge score-low" style={{ background: "rgba(244, 63, 94, 0.2)", color: "#f43f5e", border: "1px solid #f43f5e" }}>0–34: Very Weak</span>
        </div>
      </div>
      <div className="info-guide-grid">
        <div className="guide-pill">
          <div className="guide-pill-name">
            <span>📈 Technical Score (0-100)</span>
          </div>
          <div className="guide-pill-text">
            Evaluates price momentum, 5-period benchmark outperformance, volume accumulation & trend consistency.
          </div>
        </div>

        <div className="guide-pill">
          <div className="guide-pill-name">
            <span>🏢 Fundamental Score (0-100)</span>
          </div>
          <div className="guide-pill-text">
            Evaluates 4 pillars: Revenue Growth, Operating Profitability, Debt/Leverage Safety & Cash Flow Conversion.
          </div>
        </div>

        <div className="guide-pill">
          <div className="guide-pill-name">
            <span>🌐 Macro Score (0-100)</span>
          </div>
          <div className="guide-pill-text">
            LLM-based macroeconomic analysis analyzing interest rates, inflation, commodity costs & policy impacts.
          </div>
        </div>

        <div className="guide-pill">
          <div className="guide-pill-name">
            <span>⭐ Composite Rank Score</span>
          </div>
          <div className="guide-pill-text">
            Overall weighted composite: <strong>40% Fundamental + 40% Technical + 20% Macro</strong>.
          </div>
        </div>
      </div>
    </div>
  );
}

export function ScoreCell({ score }: { score: number | null | undefined }) {
  if (score == null || isNaN(score)) {
    return <span className="badge pending" style={{ fontSize: "0.72rem", padding: "2px 8px", opacity: 0.7 }} title="Score pending or not applicable">N/A</span>;
  }

  const num = Number(score);
  const formatted = num.toFixed(1);

  let label = "Neutral";
  let color = "#f59e0b";
  let badgeClass = "score-mid";

  if (num >= 80) {
    label = "Very Strong";
    color = "#10b981";
    badgeClass = "score-high";
  } else if (num >= 65) {
    label = "Strong";
    color = "#34d399";
    badgeClass = "score-high";
  } else if (num >= 50) {
    label = "Neutral";
    color = "#f59e0b";
    badgeClass = "score-mid";
  } else if (num >= 35) {
    label = "Weak";
    color = "#fb7185";
    badgeClass = "score-low";
  } else {
    label = "Very Weak";
    color = "#f43f5e";
    badgeClass = "score-low";
  }

  return (
    <div className="score-cell" title={`Score: ${formatted}/100 - Status: ${label} (${num >= 65 ? 'High momentum/health' : num >= 50 ? 'Average performance' : 'Underperforming'})`}>
      <span className="score-number" style={{ color, fontWeight: 700 }}>{formatted}</span>
      <div className="score-mini-bar" style={{ width: "50px", height: "4px", background: "#27272a", borderRadius: "2px", overflow: "hidden" }}>
        <div className="score-mini-fill" style={{ width: `${Math.max(0, Math.min(100, num))}%`, background: color, height: "100%" }} />
      </div>
      <span className={`badge ${badgeClass}`} style={{ fontSize: "0.7rem", padding: "2px 6px" }}>{label}</span>
    </div>
  );
}
