"""Run fundamental growth score logic and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_growth_score import FundamentalGrowthScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(sym, sales_val, sales_peer, np_val, np_peer, np_trans):
    calc = {
        "growth": {},
        "peer_benchmarks": {"metrics": {}}
    }
    
    if sales_val is not None:
        calc["growth"]["sales_growth_pct"] = sales_val
        calc["growth"]["sales_growth_available"] = True
    if sales_peer is not None:
        calc["peer_benchmarks"]["metrics"]["sales_growth_pct"] = {
            "available": True,
            "peer_median": sales_peer
        }
        
    if np_val is not None:
        calc["growth"]["net_profit_growth_pct"] = np_val
        calc["growth"]["net_profit_growth_available"] = True
    if np_peer is not None:
        calc["peer_benchmarks"]["metrics"]["net_profit_growth_pct"] = {
            "available": True,
            "peer_median": np_peer
        }
    if np_trans is not None:
        calc["growth"]["net_profit_transition_status"] = np_trans
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=sym,
        symbol=sym,
        calculation_details=calc
    )
    disc.add(rec)

# Add numeric example and transition example
_create("NUMERIC1", 20.0, 10.0, 15.0, 5.0, None)
_create("TRANSITION1", 10.0, 10.0, None, None, "LOSS_TO_PROFIT")
_create("UNAVAILABLE1", None, None, None, None, None)

disc.commit()

svc = FundamentalGrowthScoreService(disc)
svc.score_growth(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()

count_avail = 0
count_full_cov = 0
count_partial_cov = 0

for r in results:
    g = r.calculation_details.get("fundamental_scoring", {}).get("growth", {})
    if g.get("score") is not None:
        count_avail += 1
    if g.get("coverage_pct") == 100.0:
        count_full_cov += 1
    elif g.get("coverage_pct") == 50.0:
        count_partial_cov += 1

print(f"\n=== Fundamental Growth Score Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Growth Score Available: {count_avail}")
print(f"Full Coverage: {count_full_cov}")
print(f"Partial Coverage: {count_partial_cov}")

for sym in ["NUMERIC1", "TRANSITION1"]:
    c = next((r for r in results if r.symbol == sym), None)
    if c:
        print(f"\nExample {sym}:")
        print(json.dumps(c.calculation_details["fundamental_scoring"]["growth"], indent=2))

disc.close()
