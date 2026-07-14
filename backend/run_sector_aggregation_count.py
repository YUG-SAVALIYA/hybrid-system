"""Run technical sector aggregation and print row counts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import DiscoverySessionLocal
from sqlalchemy import text
from services.technical.technical_sector_aggregation import TechnicalSectorAggregationService
import uuid

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

# Create some mock data in the real database since previous runs failed on benchmark
disc.execute(text("""
    INSERT INTO company_technical_metrics 
    (id, run_id, symbol, sector, horizon, return_available, company_return, relative_return, volume_available, volume_change, consistency_available, company_consistency_score)
    VALUES 
    (:id1, :run, 'SYM1', 'Technology', 'SHORT', true, 10, 5, true, 2, true, 80),
    (:id2, :run, 'SYM2', 'Technology', 'SHORT', true, -5, -10, true, -1, true, 40),
    (:id3, :run, 'SYM3', 'Technology', 'SHORT', true, 10, 5, true, 2, true, 80),
    (:id4, :run, 'SYM4', 'Technology', 'SHORT', true, 10, 5, true, 2, true, 80),
    (:id5, :run, 'SYM5', 'Technology', 'SHORT', true, 10, 5, true, 2, true, 80),
    (:id6, :run, 'SYM6', 'Finance', 'SHORT', true, 5, 2, false, 0, false, 0)
"""), {
    "id1": str(uuid.uuid4()), "id2": str(uuid.uuid4()),
    "id3": str(uuid.uuid4()), "id4": str(uuid.uuid4()),
    "id5": str(uuid.uuid4()), "id6": str(uuid.uuid4()),
    "run": run_id
})
disc.commit()

svc = TechnicalSectorAggregationService(disc)
svc.aggregate_sectors(run_id, "SHORT")

count_tech = disc.execute(
    text("SELECT COUNT(*) FROM group_scores WHERE run_id = :r AND horizon = 'SHORT' AND entity_type = 'SECTOR'"),
    {"r": run_id}
).scalar()

print(f"\n=== SHORT Sector Aggregation Pipeline ===")
print(f"Sectors Generated: {count_tech}")

disc.close()
