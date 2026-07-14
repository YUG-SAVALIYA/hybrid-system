"""Run fundamental industry transition scoring and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_industry_transition_score import FundamentalIndustryTransitionScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(entity_name, np_counts, b_counts, cc_counts, std_debt_cnt=10):
    calc = {
        "fundamental": {
            "raw_aggregation": {
                "standard_debt_rule_applicable_count": std_debt_cnt,
                "transitions": {
                    "net_profit": {"counts": np_counts},
                    "borrowing": {"counts": b_counts},
                    "cash_conversion": {"counts": cc_counts}
                }
            }
        }
    }
    
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="INDUSTRY",
        entity_name=entity_name,
        parent_sector="SectorA",
        parent_industry="",
        horizon="1Y",
        calculation_details=calc
    )
    disc.add(g)

# Mixed
_create("IND_MIXED", 
    {"STANDARD_GROWTH": 4, "LOSS_TO_PROFIT": 4, "LOSS_WIDENED": 2},
    {},
    {}
)

# Financial only borrowing
_create("IND_FINANCIAL", 
    {},
    {"ZERO_TO_ZERO": 5, "INCREASED": 5},
    {},
    std_debt_cnt=0
)

disc.commit()

svc = FundamentalIndustryTransitionScoreService(disc)
svc.calculate_transition_scores(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="INDUSTRY").all()

print(f"\n=== Industry Structural Transition Pipeline ===")
print(f"Industries Processed: {len(results)}")

for name in ["IND_MIXED", "IND_FINANCIAL"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        print(f"\nExample {name}:")
        print(json.dumps(g.calculation_details["fundamental"].get("structural_transition_scores", {}), indent=2))

disc.close()
