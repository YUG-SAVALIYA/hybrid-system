"""Run final company fundamental score logic and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.company_fundamental_score import CompanyFundamentalScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(sym, g_score, p_score, fs_score, eq_score, fs_app=True):
    calc = {
        "fundamental_scoring": {},
        "peer_benchmarks": {"metrics": {}}
    }
    
    if g_score is not None:
        calc["fundamental_scoring"]["growth"] = {"score": g_score}
    if p_score is not None:
        calc["fundamental_scoring"]["profitability"] = {"score": p_score}
    
    calc["fundamental_scoring"]["financial_strength"] = {"applicable": fs_app}
    if fs_score is not None:
        calc["fundamental_scoring"]["financial_strength"]["score"] = fs_score
        
    if eq_score is not None:
        calc["fundamental_scoring"]["earnings_quality"] = {"score": eq_score}
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=sym,
        symbol=sym,
        calculation_details=calc
    )
    disc.add(rec)

# Add examples
_create("STANDARD_CO", 80.0, 70.0, 60.0, 90.0)
_create("EXCLUDED_FIN", 80.0, 70.0, None, 60.0, fs_app=False)
_create("LOW_COV", 80.0, 70.0, None, None)

disc.commit()

svc = CompanyFundamentalScoreService(disc)
svc.score_companies(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()

count_avail = 0
count_full_cov = 0
count_elig = 0
count_status = {}

for r in results:
    f = r.calculation_details.get("fundamental_scoring", {}).get("final", {})
    if f.get("score") is not None:
        count_avail += 1
    if f.get("coverage_pct") == 100.0:
        count_full_cov += 1
    if f.get("eligible_for_selection"):
        count_elig += 1
    st = f.get("status")
    count_status[st] = count_status.get(st, 0) + 1

print(f"\n=== Company Fundamental Score Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Score Available: {count_avail}")
print(f"Full Coverage: {count_full_cov}")
print(f"Eligible for Selection: {count_elig}")
print(f"Statuses: {count_status}")

for sym in ["STANDARD_CO", "EXCLUDED_FIN", "LOW_COV"]:
    c = next((r for r in results if r.symbol == sym), None)
    if c:
        print(f"\nExample {sym}:")
        print(json.dumps(c.calculation_details["fundamental_scoring"]["final"], indent=2))

disc.close()
