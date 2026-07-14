"""Run fundamental basic industry transition scoring and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_basic_industry_transition_score import FundamentalBasicIndustryTransitionScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(entity_name, np_counts, bor_counts, std_debt_cnt=10):
    calc = {
        "fundamental": {
            "raw_aggregation": {
                "standard_debt_rule_applicable_count": std_debt_cnt,
                "transitions": {
                    "net_profit": {"counts": np_counts},
                    "borrowing": {"counts": bor_counts},
                    "cash_conversion": {"counts": {}}
                }
            }
        }
    }
    
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name=entity_name,
        parent_sector="SectorA",
        parent_industry="IndA",
        horizon="1Y",
        calculation_details=calc
    )
    disc.add(g)

# Mixed net-profit example
_create("BI_MIXED", 
    np_counts={
        "STANDARD_GROWTH": 8,
        "LOSS_TO_PROFIT": 2,
        "LOSS_NARROWED": 1,
        "ZERO_BASE_TO_LOSS": 1
    },
    bor_counts={}
)

# Financial basic-industry borrowing example
_create("BI_FINANCIAL", 
    np_counts={},
    bor_counts={
        "ZERO_TO_ZERO": 4
    },
    std_debt_cnt=0
)

disc.commit()

svc = FundamentalBasicIndustryTransitionScoreService(disc)
svc.calculate_basic_industry_transitions(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="BASIC_INDUSTRY").all()

print(f"\n=== Basic Industry Transition Scoring Pipeline ===")
print(f"Basic Industries Processed: {len(results)}")

for name in ["BI_MIXED", "BI_FINANCIAL"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        trans = g.calculation_details["fundamental"].get("structural_transition_scores", {})
        print(f"\nExample {name}:")
        print(json.dumps(trans, indent=2))

disc.close()
