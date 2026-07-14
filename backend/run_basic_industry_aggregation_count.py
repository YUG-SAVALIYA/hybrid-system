"""Run technical basic industry aggregation and print row counts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import DiscoverySessionLocal
from sqlalchemy import text
from services.technical.technical_basic_industry_aggregation import TechnicalBasicIndustryAggregationService
import uuid

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

# Create mock records for basic industry
disc.execute(text("""
    INSERT INTO company_technical_metrics 
    (id, run_id, symbol, sector, industry, basic_industry, horizon, return_available, company_return, relative_return, volume_available, volume_change, consistency_available, company_consistency_score)
    VALUES 
    (:id1, :run, 'SYM1', 'Technology', 'Software', 'B2B', 'SHORT', true, 10, 5, true, 2, true, 80),
    (:id2, :run, 'SYM2', 'Technology', 'Software', 'B2B', 'SHORT', true, -5, -10, true, -1, true, 40),
    (:id3, :run, 'SYM3', 'Technology', 'Software', 'B2C', 'SHORT', true, 10, 5, true, 2, true, 80),
    (:id4, :run, 'SYM4', 'Finance', 'Banking', 'Retail', 'SHORT', true, 10, 5, false, 0, true, 80),
    (:id5, :run, 'SYM5', 'Finance', 'Banking', 'Retail', 'SHORT', true, 10, 5, true, 2, true, 80)
"""), {
    "id1": str(uuid.uuid4()), "id2": str(uuid.uuid4()),
    "id3": str(uuid.uuid4()), "id4": str(uuid.uuid4()),
    "id5": str(uuid.uuid4()),
    "run": run_id
})
disc.commit()

svc = TechnicalBasicIndustryAggregationService(disc)
svc.aggregate_basic_industries(run_id, "SHORT")

results = disc.execute(
    text("SELECT entity_name, constituent_count, warnings FROM group_scores WHERE run_id = :r AND horizon = 'SHORT' AND entity_type = 'BASIC_INDUSTRY'"),
    {"r": run_id}
).fetchall()

print(f"\n=== SHORT Basic Industry Aggregation Pipeline ===")
print(f"Basic Industries Generated: {len(results)}")
for r in results:
    w = ", ".join(r.warnings) if r.warnings else "None"
    print(f" - {r.entity_name}: {r.constituent_count} constituents. Warnings: {w}")

disc.close()
