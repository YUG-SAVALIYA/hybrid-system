"""Run technical industry scores and print row counts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import DiscoverySessionLocal
from sqlalchemy import text
from services.technical.technical_industry_score import TechnicalIndustryScoreService
import uuid

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

# Create some mock industries in the real database
disc.execute(text("""
    INSERT INTO group_scores 
    (id, run_id, entity_type, entity_name, parent_sector, parent_industry, horizon, technical_breadth_score, technical_volume_score, technical_consistency_score, calculation_details)
    VALUES 
    (:id1, :run, 'INDUSTRY', 'Hardware', 'Technology', '', 'SHORT', 80, 60, 40, '{"technical": {"return": {"median_relative_return": 10.0}}}'::jsonb),
    (:id2, :run, 'INDUSTRY', 'Software', 'Technology', '', 'SHORT', 50, null, 20, '{"technical": {"return": {"median_relative_return": 20.0}}}'::jsonb),
    (:id3, :run, 'INDUSTRY', 'Banking', 'Finance', '', 'SHORT', 90, 90, 90, '{"technical": {"return": {"median_relative_return": 15.0}}}'::jsonb)
"""), {
    "id1": str(uuid.uuid4()), "id2": str(uuid.uuid4()), "id3": str(uuid.uuid4()),
    "run": run_id
})
disc.commit()

svc = TechnicalIndustryScoreService(disc)
svc.calculate_industry_scores(run_id, "SHORT")

count_tech = disc.execute(
    text("SELECT COUNT(*) FROM group_scores WHERE run_id = :r AND horizon = 'SHORT' AND entity_type = 'INDUSTRY' AND technical_score IS NOT NULL"),
    {"r": run_id}
).scalar()

print(f"\n=== SHORT Industry Score Pipeline ===")
print(f"Industries Scored: {count_tech}")

disc.close()
