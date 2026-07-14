"""Run technical volume calculation for all horizons and print available counts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import SourceSessionLocal, DiscoverySessionLocal
from sqlalchemy import text
from services.technical.technical_date_alignment import TechnicalDateAlignmentService
from services.technical.technical_return import TechnicalReturnService
from services.technical.technical_volume import TechnicalVolumeService
import uuid

src = SourceSessionLocal()
disc = DiscoverySessionLocal()

run_id = f"test_run_{uuid.uuid4().hex[:8]}"

align_svc = TechnicalDateAlignmentService(src, disc)
ret_svc = TechnicalReturnService(src, disc)
vol_svc = TechnicalVolumeService(src, disc)

for horizon in ("SHORT", "MID", "LONG"):
    # 1. Align
    align_result = align_svc.align(horizon)
    print(f"\n=== {horizon} Volume Pipeline ===")
    print(f"Alignment Status: {align_result.status}")
    
    if align_result.status == "READY":
        # 2. Return calculation
        ret_svc.calculate_and_save_returns(run_id, align_result)
        
        # 3. Volume calculation
        vol_svc.calculate_and_save_volumes(run_id, horizon)
        
        # 4. Results
        count_ret = disc.execute(
            text("SELECT COUNT(*) FROM company_technical_metrics WHERE run_id = :r AND horizon = :h AND return_available = true"),
            {"r": run_id, "h": horizon}
        ).scalar()
        
        count_vol = disc.execute(
            text("SELECT COUNT(*) FROM company_technical_metrics WHERE run_id = :r AND horizon = :h AND volume_available = true"),
            {"r": run_id, "h": horizon}
        ).scalar()
        
        print(f"Returns Available: {count_ret}")
        print(f"Volumes Available: {count_vol}")

src.close()
disc.close()
