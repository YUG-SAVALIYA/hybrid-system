import json
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import DISCOVERY_DATABASE_URL
from models.discovery import DiscoveryRun

engine = create_engine(DISCOVERY_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

def reset_basic_industry(run_id):
    with SessionLocal() as db:
        run = db.query(DiscoveryRun).filter_by(id=run_id).first()
        if not run:
            print("Run not found.")
            return
            
        stage_results = dict(run.stage_results or {})
        
        # We delete the results of basic industry and stock selection
        if "BASIC_INDUSTRY_SELECTION" in stage_results:
            del stage_results["BASIC_INDUSTRY_SELECTION"]
        if "STOCK_SELECTION" in stage_results:
            del stage_results["STOCK_SELECTION"]
            
        run.stage_results = stage_results
        run.status = "PENDING"
        run.current_stage = "BASIC_INDUSTRY_SELECTION"
        run.error_code = None
        run.error_message = None
        run.last_completed_stage = "INDUSTRY_SELECTION"
        
        db.commit()
        print(f"Run {run_id} has been reset to run BASIC_INDUSTRY_SELECTION again!")

if __name__ == "__main__":
    reset_basic_industry("run-eb603a34cfce44ada33669962080f5e1")
