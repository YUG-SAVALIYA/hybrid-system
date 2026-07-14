"""Run fundamental sector pillar scoring and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_sector_pillar_score import FundamentalSectorPillarScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(entity_name, norm_metrics, transitions):
    calc = {
        "fundamental": {
            "metric_normalization": {"metrics": norm_metrics},
            "structural_transition_scores": transitions
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

def _m(score, app=True):
    return {"score": score, "applicable": app}

def _t(num_sc, num_cnt, fall_sc, fall_cnt):
    return {
        "numeric_status_count": num_cnt,
        "fallback_score": fall_sc,
        "fallback_status_count": fall_cnt
    }

# Mixed numeric/fallback
_create("SEC_MIXED", 
    norm_metrics={
        "net_profit_growth_pct": _m(70.0),
        "latest_ocf_to_pat": _m(None),
        "borrowing_change_pct": _m(80.0)
    },
    transitions={
        "net_profit": _t(None, 14, 90.0, 6),
        "cash_conversion": _t(None, 5, 50.0, 5),
        "borrowing": _t(None, 10, None, 5)
    }
)

# Financial only sector (N_A debt)
_create("SEC_FINANCIAL", 
    norm_metrics={
        "debt_to_equity": _m(None, app=False)
    },
    transitions={}
)

disc.commit()

svc = FundamentalSectorPillarScoreService(disc)
svc.calculate_pillar_scores(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="SECTOR").all()

print(f"\n=== Sector Pillar Scoring Pipeline ===")
print(f"Sectors Processed: {len(results)}")

for name in ["SEC_MIXED", "SEC_FINANCIAL"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        print(f"\nExample {name}:")
        print(json.dumps(g.calculation_details["fundamental"].get("pillar_scores", {}), indent=2))
        print(f"Warnings: {g.warnings}")

disc.close()
