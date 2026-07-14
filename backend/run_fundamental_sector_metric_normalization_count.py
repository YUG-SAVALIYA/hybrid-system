"""Run fundamental sector metric normalization and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_sector_metric_normalization import FundamentalSectorMetricNormalizationService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(entity_name, metrics):
    calc = {
        "fundamental": {
            "raw_aggregation": {
                "constituent_count": 10,
                "metrics": metrics
            }
        }
    }
    
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="SECTOR",
        entity_name=entity_name,
        parent_sector="",
        parent_industry="",
        horizon="1Y",
        calculation_details=calc
    )
    disc.add(g)

def _m(median, valid=5, app=5, cov=100.0, reason=None):
    return {
        "median": median,
        "valid_count": valid,
        "applicable_count": app,
        "coverage_pct": cov,
        "reason": reason
    }

# Sales Growth (higher is better)
# Sector A: 10.0
# Sector B: 20.0
# Sector C: 20.0
# Sector D: 30.0
_create("SEC_A", {"sales_growth_pct": _m(10.0), "debt_to_equity": _m(1.0)})
_create("SEC_B", {"sales_growth_pct": _m(20.0), "debt_to_equity": _m(0.8)})
_create("SEC_C", {"sales_growth_pct": _m(20.0), "debt_to_equity": _m(0.8)})
_create("SEC_D", {"sales_growth_pct": _m(30.0), "debt_to_equity": _m(0.2)})

# Single sector
_create("SEC_SINGLE", {"net_profit_growth_pct": _m(5.0)})

# Missing & invalid
_create("SEC_INVALID", {
    "sales_growth_pct": _m(15.0, valid=2),
    "debt_to_equity": _m(None, valid=0, app=0, cov=None, reason="N_A_NO_STANDARD_DEBT_RULE_COMPANIES")
})

disc.commit()

svc = FundamentalSectorMetricNormalizationService(disc)
svc.normalize_metrics(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="SECTOR").all()

print(f"\n=== Sector Metric Normalization Pipeline ===")
print(f"Sectors Processed: {len(results)}")

for name in ["SEC_A", "SEC_B", "SEC_D", "SEC_SINGLE", "SEC_INVALID"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        print(f"\nExample {name}:")
        print(json.dumps(g.calculation_details["fundamental"].get("metric_normalization", {}), indent=2))

disc.close()
