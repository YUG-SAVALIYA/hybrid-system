"""Run technical basic industry scores and print row counts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import DiscoverySessionLocal
from sqlalchemy import text
from services.technical.technical_basic_industry_score import TechnicalBasicIndustryScoreService
import uuid

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

# Create some mock basic industries
disc.execute(text("""
    INSERT INTO group_scores 
    (id, run_id, entity_type, entity_name, parent_sector, parent_industry, horizon, technical_breadth_score, technical_volume_score, technical_consistency_score, calculation_details)
    VALUES 
    (:id1, :run, 'BASIC_INDUSTRY', 'B2B', 'Technology', 'Software', 'SHORT', 80, 60, 40, '{"technical": {"return": {"median_relative_return": 10.0}}}'::jsonb),
    (:id2, :run, 'BASIC_INDUSTRY', 'B2C', 'Technology', 'Software', 'SHORT', 50, null, 20, '{"technical": {"return": {"median_relative_return": 20.0}}}'::jsonb),
    (:id3, :run, 'BASIC_INDUSTRY', 'Retail', 'Finance', 'Banking', 'SHORT', 90, 90, 90, '{"technical": {"return": {"median_relative_return": 15.0}}}'::jsonb)
"""), {
    "id1": str(uuid.uuid4()), "id2": str(uuid.uuid4()), "id3": str(uuid.uuid4()),
    "run": run_id
})
disc.commit()

svc = TechnicalBasicIndustryScoreService(disc)
svc.calculate_basic_industry_scores(run_id, "SHORT")

count_tech = disc.execute(
    text("SELECT COUNT(*) FROM group_scores WHERE run_id = :r AND horizon = 'SHORT' AND entity_type = 'BASIC_INDUSTRY' AND technical_score IS NOT NULL"),
    {"r": run_id}
).scalar()

print(f"\n=== SHORT Basic Industry Score Pipeline ===")
print(f"Basic Industries Scored: {count_tech}")

disc.close()
