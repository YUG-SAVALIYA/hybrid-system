"""Run fundamental peer median logic and print results using mocked DB connection."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_peer_median import FundamentalPeerMedianService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(sym, sec, ind, bas, val):
    calc = {
        "growth": {},
        "profitability": {},
        "financial_strength": {"standard_debt_rule_applicable": True},
        "earnings_quality": {"cash_conversion": {}, "profit_stability": {}}
    }
    if val is not None:
        calc["growth"]["sales_growth_pct"] = val
        calc["growth"]["sales_growth_available"] = True
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=sym,
        symbol=sym,
        sector=sec,
        industry=ind,
        basic_industry=bas,
        calculation_details=calc
    )
    disc.add(rec)

# Add some test data
_create("C1", "SecA", "IndA", "BasA", 10.0)
_create("B1", "SecA", "IndA", "BasA", 8.0)
_create("B2", "SecA", "IndA", "BasA", 9.0)
_create("B3", "SecA", "IndA", "BasA", 11.0)
_create("I1", "SecA", "IndA", "BasB", 12.0)
_create("S1", "SecA", "IndB", "BasC", 14.0)

disc.commit()

svc = FundamentalPeerMedianService(disc)
svc.resolve_peer_medians(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
c1 = next((r for r in results if r.symbol == "C1"), None)

resolved_at_basic = 0
resolved_at_industry = 0
resolved_at_sector = 0
unavailable = 0

for r in results:
    pb = r.calculation_details.get("peer_benchmarks", {})
    m = pb.get("metrics", {}).get("sales_growth_pct", {})
    lvl = m.get("comparison_level")
    if lvl == "BASIC_INDUSTRY": resolved_at_basic += 1
    elif lvl == "INDUSTRY": resolved_at_industry += 1
    elif lvl == "SECTOR": resolved_at_sector += 1
    else: unavailable += 1

print(f"\n=== Fundamental Peer Median Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Resolved at BASIC_INDUSTRY: {resolved_at_basic}")
print(f"Resolved at INDUSTRY: {resolved_at_industry}")
print(f"Resolved at SECTOR: {resolved_at_sector}")
print(f"Unavailable: {unavailable}")
if c1:
    print(f"\nExample Company C1:")
    import json
    print(json.dumps(c1.calculation_details["peer_benchmarks"], indent=2))

disc.close()
