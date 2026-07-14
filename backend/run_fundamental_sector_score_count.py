"""Run fundamental sector final scoring and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_sector_score import FundamentalSectorScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(entity_name, pillars, cnt=10):
    calc = {
        "fundamental": {
            "raw_aggregation": {
                "fundamental_score_available_count": cnt
            },
            "pillar_scores": pillars
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

def _p(score, app=True, stat="NEUTRAL"):
    if not app:
        return {"score": None, "applicable": False, "status": "N_A", "reason": "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"}
    if score is None:
        return {"score": None, "applicable": True, "status": "UNAVAILABLE"}
    return {"score": score, "applicable": True, "status": stat}

# Standard Sector
_create("SEC_STANDARD", {
    "growth": _p(80.0, stat="VERY_STRONG"),
    "profitability": _p(70.0, stat="STRONG"),
    "financial_strength": _p(60.0, stat="NEUTRAL"),
    "earnings_quality": _p(40.0, stat="WEAK")
})

# Financial Sector (N_A debt)
_create("SEC_FINANCIAL", {
    "growth": _p(80.0, stat="VERY_STRONG"),
    "profitability": _p(70.0, stat="STRONG"),
    "financial_strength": _p(None, app=False),
    "earnings_quality": _p(60.0, stat="NEUTRAL")
})

disc.commit()

svc = FundamentalSectorScoreService(disc)
svc.calculate_final_scores(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="SECTOR").all()

print(f"\n=== Sector Final Scoring Pipeline ===")
print(f"Sectors Processed: {len(results)}")

for name in ["SEC_STANDARD", "SEC_FINANCIAL"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        print(f"\nExample {name}:")
        print(json.dumps(g.calculation_details["fundamental"].get("final_score", {}), indent=2))
        print(f"Warnings: {g.warnings}")
        print(f"GroupScore Column score: {g.fundamental_score}")

disc.close()
