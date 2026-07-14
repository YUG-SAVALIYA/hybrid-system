"""Run fundamental industry metric normalization and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_industry_metric_normalization import FundamentalIndustryMetricNormalizationService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(entity_name, parent_sec, metrics, avail_cnt=5):
    calc = {
        "fundamental": {
            "raw_aggregation": {
                "fundamental_score_available_count": avail_cnt,
                "metrics": metrics
            }
        }
    }
    
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="INDUSTRY",
        entity_name=entity_name,
        parent_sector=parent_sec,
        parent_industry="",
        horizon="1Y",
        calculation_details=calc
    )
    disc.add(g)

def _m(med, v=5, a=5, c=100.0, r=None):
    return {"median": med, "valid_count": v, "applicable_count": a, "coverage_pct": c, "reason": r}

# Technology
_create("Software", "Technology", {
    "sales_growth_pct": _m(30.0),
    "debt_to_equity": _m(0.2)
})
_create("Hardware", "Technology", {
    "sales_growth_pct": _m(15.0),
    "debt_to_equity": _m(0.8)
})
_create("Semiconductors", "Technology", {
    "sales_growth_pct": _m(25.0),
    "debt_to_equity": _m(0.5)
})

# Financials
_create("Banking", "Financials", {
    "sales_growth_pct": _m(10.0),
    "debt_to_equity": _m(None, v=0, a=0, c=None, r="N_A_NO_STANDARD_DEBT_RULE_COMPANIES"),
    "borrowing_change_pct": _m(None, v=0, a=0, c=None, r="N_A_NO_STANDARD_DEBT_RULE_COMPANIES")
})
_create("Insurance", "Financials", {
    "sales_growth_pct": _m(12.0),
    "debt_to_equity": _m(None, v=0, a=0, c=None, r="N_A_NO_STANDARD_DEBT_RULE_COMPANIES"),
    "borrowing_change_pct": _m(None, v=0, a=0, c=None, r="N_A_NO_STANDARD_DEBT_RULE_COMPANIES")
})

disc.commit()

svc = FundamentalIndustryMetricNormalizationService(disc)
svc.normalize_industry_metrics(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="INDUSTRY").all()

print(f"\n=== Industry Metric Normalization Pipeline ===")
print(f"Industries Processed: {len(results)}")

for name in ["Software", "Banking"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        norm = g.calculation_details["fundamental"].get("metric_normalization", {})
        print(f"\nExample {name}:")
        print(json.dumps(norm, indent=2))

disc.close()
