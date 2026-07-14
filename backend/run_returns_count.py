"""Run technical return calculation for all horizons and print available counts."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import SourceSessionLocal, DiscoverySessionLocal
from sqlalchemy import text
from services.technical.technical_date_alignment import TechnicalDateAlignmentService
from services.technical.technical_return import TechnicalReturnService
import uuid

src = SourceSessionLocal()
disc = DiscoverySessionLocal()

run_id = f"test_run_{uuid.uuid4().hex[:8]}"

align_svc = TechnicalDateAlignmentService(src, disc)
ret_svc = TechnicalReturnService(src, disc)

for horizon in ("SHORT", "MID", "LONG"):
    align_result = align_svc.align(horizon)
    print(f"\n=== {horizon} Alignment ===")
    print(f"Status: {align_result.status}")
    if align_result.status == "READY":
        print(f"As of: {align_result.as_of_date}")
        print(f"Total companies: {len(align_result.companies)}")
        available = sum(1 for c in align_result.companies if c.available)
        print(f"Available aligned: {available}")
        
        # Now run returns
        ret_svc.calculate_and_save_returns(run_id, align_result)
        
        # Check DB for successful returns
        count = disc.execute(
            text("SELECT COUNT(*) FROM company_technical_metrics WHERE run_id = :r AND horizon = :h AND return_available = true"),
            {"r": run_id, "h": horizon}
        ).scalar()
        print(f"Return Available in DB: {count}")

        # Check total rows inserted for this horizon
        total_rows = disc.execute(
            text("SELECT COUNT(*) FROM company_technical_metrics WHERE run_id = :r AND horizon = :h"),
            {"r": run_id, "h": horizon}
        ).scalar()
        print(f"Total rows inserted: {total_rows}")

src.close()
disc.close()
