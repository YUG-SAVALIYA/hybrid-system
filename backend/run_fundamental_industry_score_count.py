"""Run fundamental industry scoring and print results."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import uuid
import json
from database import DiscoverySessionLocal
from models.discovery import GroupScore
from services.fundamental.fundamental_industry_score import FundamentalIndustryScoreService

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

def _create(entity_name, pillars, avail_cnt=5):
    calc = {
        "fundamental": {
            "raw_aggregation": {
                "fundamental_score_available_count": avail_cnt
            },
            "pillar_scores": pillars
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

def _p(score, app=True):
    stat = "NEUTRAL"
    if score is None:
        stat = "UNAVAILABLE" if app else "N_A"
    elif score >= 80: stat = "VERY_STRONG"
    elif score >= 65: stat = "STRONG"
    elif score >= 35: stat = "WEAK"
    elif score < 35: stat = "VERY_WEAK"
    return {"score": score, "applicable": app, "status": stat}

# Standard industry, all available
_create("IND_STANDARD", {
    "growth": _p(80.0),
    "profitability": _p(70.0),
    "financial_strength": _p(60.0),
    "earnings_quality": _p(50.0)
})

# Financial industry, EQ missing
_create("IND_FINANCIAL", {
    "growth": _p(80.0),
    "profitability": _p(70.0),
    "financial_strength": _p(None, app=False),
    "earnings_quality": _p(None)
})

disc.commit()

svc = FundamentalIndustryScoreService(disc)
svc.calculate_industry_scores(run_id)

results = disc.query(GroupScore).filter_by(run_id=run_id, entity_type="INDUSTRY").all()

print(f"\n=== Final Industry Score Pipeline ===")
print(f"Industries Processed: {len(results)}")

for name in ["IND_STANDARD", "IND_FINANCIAL"]:
    g = next((r for r in results if r.entity_name == name), None)
    if g:
        print(f"\nExample {name}:")
        print(json.dumps(g.calculation_details["fundamental"].get("final_score", {}), indent=2))
        print(f"Warnings: {g.warnings}")
        print(f"group_scores.fundamental_score = {g.fundamental_score}")

disc.close()
