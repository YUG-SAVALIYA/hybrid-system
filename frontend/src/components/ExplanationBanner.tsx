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
      <div className="info-banner-desc">
        Every sector, industry, and stock is evaluated using an automated multi-factor scoring model rated from <strong>0 to 100</strong>.
      </div>
      <div className="info-guide-grid">
        <div className="guide-pill">
          <div className="guide-pill-name">
            <span>📈 Technical Score</span>
            <span className="score-high">0-100</span>
          </div>
          <div className="guide-pill-text">
            Measures 5-period price momentum, moving average trends, and return consistency.
          </div>
        </div>

        <div className="guide-pill">
          <div className="guide-pill-name">
            <span>🏢 Fundamental Score</span>
            <span className="score-high">0-100</span>
          </div>
          <div className="guide-pill-text">
            Evaluates revenue growth, profit margin, ROE, debt ratio & peer valuations.
          </div>
        </div>

        <div className="guide-pill">
          <div className="guide-pill-name">
            <span>⭐ Final Composite Score</span>
            <span className="score-high">0-100</span>
          </div>
          <div className="guide-pill-text">
            Weighted index combining Technical Trends + Fundamental Health.
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

  let label = "Moderate";
  let color = "var(--warning)";
  let bg = "rgba(245, 158, 11, 0.12)";
  let badgeClass = "score-mid";

  if (num >= 75) {
    label = "Strong";
    color = "#10b981";
    bg = "rgba(16, 185, 129, 0.12)";
    badgeClass = "score-high";
  } else if (num >= 85) {
    label = "Exceptional";
    color = "#10b981";
    bg = "rgba(16, 185, 129, 0.12)";
    badgeClass = "score-high";
  } else if (num < 50) {
    label = "Weak";
    color = "#f43f5e";
    bg = "rgba(244, 63, 94, 0.12)";
    badgeClass = "score-low";
  }

  return (
    <div className="score-cell" title={`Score: ${formatted}/100 (${label})`}>
      <span className="score-number" style={{ color }}>{formatted}</span>
      <div className="score-mini-bar">
        <div className="score-mini-fill" style={{ width: `${Math.max(0, Math.min(100, num))}%`, background: color }} />
      </div>
      <span className={`badge ${badgeClass}`} style={{ fontSize: "0.7rem", padding: "2px 6px" }}>{label}</span>
    </div>
  );
}
