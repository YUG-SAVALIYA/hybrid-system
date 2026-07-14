"""Run fundamental profitability score logic and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_profitability_score import FundamentalProfitabilityScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(sym, om_val, om_peer, mt_val, mt_peer):
    calc = {
        "profitability": {},
        "peer_benchmarks": {"metrics": {}}
    }
    
    if om_val is not None:
        calc["profitability"]["latest_operating_margin_pct"] = om_val
        calc["profitability"]["latest_operating_margin_available"] = True
    if om_peer is not None:
        calc["peer_benchmarks"]["metrics"]["latest_operating_margin_pct"] = {
            "available": True,
            "peer_median": om_peer
        }
        
    if mt_val is not None:
        calc["profitability"]["operating_margin_change_pp"] = mt_val
        calc["profitability"]["operating_margin_trend_available"] = True
    if mt_peer is not None:
        calc["peer_benchmarks"]["metrics"]["operating_margin_change_pp"] = {
            "available": True,
            "peer_median": mt_peer
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
_create("FULL_COV", 18.0, 12.0, 2.0, 1.0)
_create("PARTIAL_COV", 18.0, 12.0, None, None)
_create("UNAVAILABLE", None, None, None, None)

disc.commit()

svc = FundamentalProfitabilityScoreService(disc)
svc.score_profitability(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()

count_avail = 0
count_full_cov = 0
count_partial_cov = 0

for r in results:
    g = r.calculation_details.get("fundamental_scoring", {}).get("profitability", {})
    if g.get("score") is not None:
        count_avail += 1
    if g.get("coverage_pct") == 100.0:
        count_full_cov += 1
    elif g.get("coverage_pct") > 0.0 and g.get("coverage_pct") < 100.0:
        count_partial_cov += 1

print(f"\n=== Fundamental Profitability Score Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Profitability Score Available: {count_avail}")
print(f"Full Coverage: {count_full_cov}")
print(f"Partial Coverage: {count_partial_cov}")

for sym in ["FULL_COV", "PARTIAL_COV"]:
    c = next((r for r in results if r.symbol == sym), None)
    if c:
        print(f"\nExample {sym}:")
        print(json.dumps(c.calculation_details["fundamental_scoring"]["profitability"], indent=2))

disc.close()
