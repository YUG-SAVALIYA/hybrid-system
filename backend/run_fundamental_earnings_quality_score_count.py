"""Run fundamental earnings quality score logic and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_earnings_quality_score import FundamentalEarningsQualityScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(sym, cc_val, cc_peer, cc_status, cct_val, cct_peer, ph_val, vol_val, vol_peer):
    calc = {
        "earnings_quality": {
            "cash_conversion": {},
            "profit_stability": {}
        },
        "peer_benchmarks": {"metrics": {}}
    }
    
    if cc_val is not None:
        calc["earnings_quality"]["cash_conversion"]["latest_ocf_to_pat"] = cc_val
        calc["earnings_quality"]["cash_conversion"]["latest_ocf_to_pat_available"] = True
    if cc_peer is not None:
        calc["peer_benchmarks"]["metrics"]["latest_ocf_to_pat"] = {
            "available": True,
            "peer_median": cc_peer
        }
    if cc_status is not None:
        calc["earnings_quality"]["cash_conversion"]["latest_cash_conversion_status"] = cc_status
        
    if cct_val is not None:
        calc["earnings_quality"]["cash_conversion"]["ocf_to_pat_change"] = cct_val
        calc["earnings_quality"]["cash_conversion"]["ocf_to_pat_change_available"] = True
    if cct_peer is not None:
        calc["peer_benchmarks"]["metrics"]["ocf_to_pat_change"] = {
            "available": True,
            "peer_median": cct_peer
        }

    if ph_val is not None:
        calc["earnings_quality"]["profit_stability"]["positive_pat_period_ratio"] = ph_val
        calc["earnings_quality"]["profit_stability"]["profit_stability_available"] = True

    if vol_val is not None:
        calc["earnings_quality"]["profit_stability"]["pat_growth_volatility_pct"] = vol_val
        calc["earnings_quality"]["profit_stability"]["pat_growth_volatility_available"] = True
    if vol_peer is not None:
        calc["peer_benchmarks"]["metrics"]["pat_growth_volatility_pct"] = {
            "available": True,
            "peer_median": vol_peer
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
_create("ALL_4", 1.2, 0.8, None, 0.3, 0.1, 80.0, 20.0, 40.0)
_create("FALLBACK", None, None, "LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW", None, None, 80.0, None, None)

disc.commit()

svc = FundamentalEarningsQualityScoreService(disc)
svc.score_earnings_quality(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()

count_avail = 0
count_full_cov = 0
count_partial = 0

for r in results:
    g = r.calculation_details.get("fundamental_scoring", {}).get("earnings_quality", {})
    if g.get("score") is not None:
        count_avail += 1
    if g.get("coverage_pct") == 100.0:
        count_full_cov += 1
    elif g.get("coverage_pct") > 0.0 and g.get("coverage_pct") < 100.0:
        count_partial += 1

print(f"\n=== Fundamental Earnings Quality Score Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Score Available: {count_avail}")
print(f"Full Coverage: {count_full_cov}")
print(f"Partial Coverage: {count_partial}")

for sym in ["ALL_4", "FALLBACK"]:
    c = next((r for r in results if r.symbol == sym), None)
    if c:
        print(f"\nExample {sym}:")
        print(json.dumps(c.calculation_details["fundamental_scoring"]["earnings_quality"], indent=2))

disc.close()
