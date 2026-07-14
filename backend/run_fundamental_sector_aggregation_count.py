"""Run fundamental sector aggregation and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric, GroupScore
from services.fundamental.fundamental_sector_aggregation import FundamentalSectorAggregationService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(sym, sector, fund_score, std_debt, sg, np_trans, dte):
    calc = {
        "financial_strength": {"standard_debt_rule_applicable": std_debt},
        "growth": {},
        "profitability": {},
        "earnings_quality": {"cash_conversion": {}, "profit_stability": {}}
    }
    
    if sg is not None:
        calc["growth"]["sales_growth_pct"] = sg
        calc["growth"]["sales_growth_pct_available"] = True
    if np_trans is not None:
        calc["growth"]["net_profit_transition"] = np_trans
        
    if dte is not None:
        calc["financial_strength"]["debt_to_equity"] = dte
        calc["financial_strength"]["debt_to_equity_available"] = True
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=sym,
        symbol=sym,
        sector=sector,
        final_fundamental_score=fund_score,
        fundamental_eligible_for_selection=(fund_score is not None),
        calculation_details=calc
    )
    disc.add(rec)

# Create 6 companies in a normal sector
for i in range(6):
    _create(f"NORM_{i}", "Normal Sector", 70.0, True, 10.0 + i*2.0, "STANDARD_GROWTH", 0.5 + i*0.1)

# Create 3 companies in a financial sector
for i in range(3):
    _create(f"FIN_{i}", "Financial Sector", 80.0, False, 15.0 + i*5.0, "LOSS_TO_PROFIT", 5.0)

disc.commit()

svc = FundamentalSectorAggregationService(disc)
svc.aggregate_sectors(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="SECTOR").all()

print(f"\n=== Fundamental Sector Aggregation Pipeline ===")
print(f"Sectors Processed: {len(results)}")
for r in results:
    agg = r.calculation_details.get("fundamental", {}).get("raw_aggregation", {})
    w = "INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS" in (r.warnings or [])
    print(f"Sector: {r.entity_name} | Constituent Count: {agg.get('constituent_count')} | Insufficient: {w}")

for name in ["Normal Sector", "Financial Sector"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        print(f"\nExample {name}:")
        print(json.dumps(g.calculation_details["fundamental"]["raw_aggregation"], indent=2))

disc.close()
