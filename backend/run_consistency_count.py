"""Run technical consistency calculation for all horizons and print available counts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import SourceSessionLocal, DiscoverySessionLocal
from sqlalchemy import text
from services.technical.technical_date_alignment import TechnicalDateAlignmentService
from services.technical.technical_return import TechnicalReturnService
from services.technical.technical_volume import TechnicalVolumeService
from services.technical.technical_consistency import TechnicalConsistencyService
import uuid

src = SourceSessionLocal()
disc = DiscoverySessionLocal()

run_id = f"test_run_{uuid.uuid4().hex[:8]}"

align_svc = TechnicalDateAlignmentService(src, disc)
ret_svc = TechnicalReturnService(src, disc)
vol_svc = TechnicalVolumeService(src, disc)
cons_svc = TechnicalConsistencyService(src, disc)

for horizon in ("SHORT", "MID", "LONG"):
    align_result = align_svc.align(horizon)
    print(f"\n=== {horizon} Consistency Pipeline ===")
    print(f"Alignment Status: {align_result.status}")
    
    if align_result.status == "READY":
        ret_svc.calculate_and_save_returns(run_id, align_result)
        vol_svc.calculate_and_save_volumes(run_id, horizon)
        cons_svc.calculate_and_save_consistency(run_id, horizon)
        
        count_cons = disc.execute(
            text("SELECT COUNT(*) FROM company_technical_metrics WHERE run_id = :r AND horizon = :h AND consistency_available = true"),
            {"r": run_id, "h": horizon}
        ).scalar()
        
        print(f"Consistency Available: {count_cons}")

src.close()
disc.close()
