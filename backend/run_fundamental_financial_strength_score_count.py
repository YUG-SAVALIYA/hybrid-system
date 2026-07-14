"""Run fundamental financial strength score logic and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_financial_strength_score import FundamentalFinancialStrengthScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(sym, std_debt, dte_val, dte_peer, bt_val, bt_peer):
    calc = {
        "financial_strength": {
            "standard_debt_rule_applicable": std_debt
        },
        "peer_benchmarks": {"metrics": {}}
    }
    
    if dte_val is not None:
        calc["financial_strength"]["debt_to_equity"] = dte_val
        calc["financial_strength"]["debt_to_equity_available"] = True
    if dte_peer is not None:
        calc["peer_benchmarks"]["metrics"]["debt_to_equity"] = {
            "available": True,
            "peer_median": dte_peer
        }
        
    if bt_val is not None:
        calc["financial_strength"]["borrowing_change_pct"] = bt_val
        calc["financial_strength"]["borrowing_trend_available"] = True
    if bt_peer is not None:
        calc["peer_benchmarks"]["metrics"]["borrowing_change_pct"] = {
            "available": True,
            "peer_median": bt_peer
        }
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=sym,
        symbol=sym,
        calculation_details=calc
    )
    disc.add(rec)

# Add numeric examples
_create("STANDARD_CO", True, 0.4, 0.8, -20.0, 10.0)
_create("EXCLUDED_FIN", False, 5.0, 5.0, 10.0, 10.0)

disc.commit()

svc = FundamentalFinancialStrengthScoreService(disc)
svc.score_financial_strength(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()

count_app = 0
count_avail = 0
count_full_cov = 0

for r in results:
    g = r.calculation_details.get("fundamental_scoring", {}).get("financial_strength", {})
    if g.get("applicable"):
        count_app += 1
    if g.get("score") is not None:
        count_avail += 1
    if g.get("coverage_pct") == 100.0:
        count_full_cov += 1

print(f"\n=== Fundamental Financial Strength Score Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Applicable Standard Companies: {count_app}")
print(f"Score Available: {count_avail}")
print(f"Full Coverage: {count_full_cov}")

for sym in ["STANDARD_CO", "EXCLUDED_FIN"]:
    c = next((r for r in results if r.symbol == sym), None)
    if c:
        print(f"\nExample {sym}:")
        print(json.dumps(c.calculation_details["fundamental_scoring"]["financial_strength"], indent=2))

disc.close()
