import json
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import DISCOVERY_DATABASE_URL
from models.discovery import DiscoveryRun

engine = create_engine(DISCOVERY_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

def reset_rankings(run_id):
    with SessionLocal() as db:
        run = db.query(DiscoveryRun).filter_by(id=run_id).first()
        if not run:
            print("Run not found.")
            return
            
        stage_results = dict(run.stage_results or {})
        
        # We delete the results of ranking stages
        stages_to_reset = ["SECTOR_SELECTION", "INDUSTRY_SELECTION", "BASIC_INDUSTRY_SELECTION", "STOCK_SELECTION"]
        for stage in stages_to_reset:
            if stage in stage_results:
                del stage_results[stage]
            
        run.stage_results = stage_results
        run.status = "PENDING"
        run.current_stage = "SECTOR_SELECTION"
        run.error_code = None
        run.error_message = None
        run.last_completed_stage = "MACRO_FILTER"
        
        db.commit()
        print(f"Run {run_id} has been reset to run rankings again!")

if __name__ == "__main__":
    reset_rankings("run-eb603a34cfce44ada33669962080f5e1")
