"""Run technical sector scores and print row counts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import DiscoverySessionLocal
from sqlalchemy import text
from services.technical.technical_sector_score import TechnicalSectorScoreService
import uuid

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

# Create some mock sectors in the real database
disc.execute(text("""
    INSERT INTO group_scores 
    (id, run_id, entity_type, entity_name, parent_sector, parent_industry, horizon, technical_breadth_score, technical_volume_score, technical_consistency_score, calculation_details)
    VALUES 
    (:id1, :run, 'SECTOR', 'Technology', '', '', 'SHORT', 80, 60, 40, '{"median_relative_return": 10.0}'::jsonb),
    (:id2, :run, 'SECTOR', 'Finance', '', '', 'SHORT', 50, null, 20, '{"median_relative_return": 5.0}'::jsonb),
    (:id3, :run, 'SECTOR', 'Healthcare', '', '', 'SHORT', 90, 90, 90, '{"median_relative_return": 15.0}'::jsonb)
"""), {
    "id1": str(uuid.uuid4()), "id2": str(uuid.uuid4()), "id3": str(uuid.uuid4()),
    "run": run_id
})
disc.commit()

svc = TechnicalSectorScoreService(disc)
svc.calculate_sector_scores(run_id, "SHORT")

count_tech = disc.execute(
    text("SELECT COUNT(*) FROM group_scores WHERE run_id = :r AND horizon = 'SHORT' AND entity_type = 'SECTOR' AND technical_score IS NOT NULL"),
    {"r": run_id}
).scalar()

print(f"\n=== SHORT Sector Score Pipeline ===")
print(f"Sectors Scored: {count_tech}")

disc.close()
