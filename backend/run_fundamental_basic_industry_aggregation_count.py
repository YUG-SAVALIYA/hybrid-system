"""Run fundamental basic industry aggregation and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric, GroupScore
from services.fundamental.fundamental_basic_industry_aggregation import FundamentalBasicIndustryAggregationService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(sym, sec, ind, bi, fund_score, std_debt, sg, np_trans, dte):
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
        sector=sec,
        industry=ind,
        basic_industry=bi,
        final_fundamental_score=fund_score,
        fundamental_eligible_for_selection=(fund_score is not None),
        calculation_details=calc
    )
    disc.add(rec)

# Standard basic industry
for i in range(4):
    _create(f"STD_{i}", "SecA", "IndA", "BI_Standard", 70.0, True, 10.0 + i*5.0, "STANDARD_GROWTH", 0.5)

# Financial basic industry
for i in range(3):
    _create(f"FIN_{i}", "SecB", "IndB", "BI_Financial", 80.0, False, 15.0, "LOSS_TO_PROFIT", 5.0)

disc.commit()

svc = FundamentalBasicIndustryAggregationService(disc)
svc.aggregate_basic_industries(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="BASIC_INDUSTRY").all()

print(f"\n=== Basic Industry Fundamental Aggregation Pipeline ===")
print(f"Basic Industries Processed: {len(results)}")

for name in ["BI_Standard", "BI_Financial"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        agg = g.calculation_details["fundamental"].get("raw_aggregation", {})
        print(f"\nExample {name}:")
        print(json.dumps(agg, indent=2))
        print(f"Warnings: {g.warnings}")

disc.close()
