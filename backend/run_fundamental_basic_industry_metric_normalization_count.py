"""Run fundamental basic industry metric normalization and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_basic_industry_metric_normalization import FundamentalBasicIndustryMetricNormalizationService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(entity_name, p_sec, p_ind, metrics, avail_cnt=5):
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
        entity_type="BASIC_INDUSTRY",
        entity_name=entity_name,
        parent_sector=p_sec,
        parent_industry=p_ind,
        horizon="1Y",
        calculation_details=calc
    )
    disc.add(g)

def _m(med, v=5, a=5, c=100.0, r=None):
    return {"median": med, "valid_count": v, "applicable_count": a, "coverage_pct": c, "reason": r}

# Technology -> Software (2 siblings)
_create("B2B Software", "Technology", "Software", {
    "sales_growth_pct": _m(30.0),
    "debt_to_equity": _m(0.2)
})
_create("B2C Software", "Technology", "Software", {
    "sales_growth_pct": _m(15.0),
    "debt_to_equity": _m(0.8)
})

# Financials -> Banking (1 sibling)
_create("Retail Banking", "Financials", "Banking", {
    "sales_growth_pct": _m(10.0),
    "debt_to_equity": _m(None, v=0, a=0, c=None, r="N_A_NO_STANDARD_DEBT_RULE_COMPANIES"),
    "borrowing_change_pct": _m(None, v=0, a=0, c=None, r="N_A_NO_STANDARD_DEBT_RULE_COMPANIES")
})

disc.commit()

svc = FundamentalBasicIndustryMetricNormalizationService(disc)
svc.normalize_basic_industry_metrics(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="BASIC_INDUSTRY").all()

print(f"\n=== Basic Industry Metric Normalization Pipeline ===")
print(f"Basic Industries Processed: {len(results)}")

for name in ["B2B Software", "Retail Banking"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        norm = g.calculation_details["fundamental"].get("metric_normalization", {})
        print(f"\nExample {name}:")
        print(json.dumps(norm, indent=2))

disc.close()
