import sys
import os
import uuid
import datetime

# Add backend directory to Python path so we can import from app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import DiscoverySessionLocal
from models.discovery import (
    DiscoveryRun, GroupScore, DiscoverySelection, 
    StockCandidateSnapshot, EligibleUniverseSnapshot,
    CompanyTechnicalMetric, CompanyFundamentalMetric
)

def seed_db():
    db = DiscoverySessionLocal()
    try:
        run_id = "run-dummy-1234"
        horizon = "SHORT"
        
        # 1. Clean existing records for this dummy run
        db.query(DiscoveryRun).filter_by(id=run_id).delete()
        db.query(GroupScore).filter_by(run_id=run_id).delete()
        db.query(DiscoverySelection).filter_by(run_id=run_id).delete()
        db.query(StockCandidateSnapshot).filter_by(run_id=run_id).delete()
        db.query(EligibleUniverseSnapshot).filter_by(run_id=run_id).delete()
        db.query(CompanyTechnicalMetric).filter_by(run_id=run_id).delete()
        db.query(CompanyFundamentalMetric).filter_by(run_id=run_id).delete()

        # 2. Seed the run itself, marked as completed
        run = DiscoveryRun(
            id=run_id,
            status="COMPLETED",
            horizon=horizon,
            current_stage="COMPLETED",
            last_completed_stage="COMPLETED",
            stage_results={
                "SECTOR_SELECTION": {"status": "COMPLETED", "horizons": {"SHORT": {"status": "COMPLETED"}}},
                "INDUSTRY_SELECTION": {"status": "COMPLETED", "horizons": {"SHORT": {"status": "COMPLETED"}}},
                "BASIC_INDUSTRY_SELECTION": {"status": "COMPLETED", "horizons": {"SHORT": {"status": "COMPLETED"}}},
                "STOCK_SELECTION": {"status": "COMPLETED", "horizons": {"SHORT": {"status": "COMPLETED"}}},
            },
            warnings=[]
        )
        db.add(run)

        # 3. Seed Sectors
        sectors = [
            {"name": "Technology", "rank": 1, "tech": 88.2, "fund": 75.5, "macro": 82.0, "final": 85.4},
            {"name": "Healthcare", "rank": 2, "tech": 65.0, "fund": 85.0, "macro": 75.0, "final": 70.1},
            {"name": "Energy", "rank": 3, "tech": 40.2, "fund": 55.5, "macro": 32.0, "final": 45.4}
        ]
        for s in sectors:
            gs = GroupScore(
                id=str(uuid.uuid4()), run_id=run_id, entity_type="SECTOR", entity_name=s["name"], horizon=horizon,
                technical_score=s["tech"], fundamental_score=s["fund"], macro_score=s["macro"], final_score=s["final"], rank=s["rank"],
                technical_return_score=90.0, technical_breadth_score=82.5, technical_volume_score=70.2, technical_consistency_score=92.0,
                calculation_details={
                    "fundamental": {
                        "raw_aggregation": {"metrics": {"sales_growth_pct": {"median": 25.2}, "net_profit_growth_pct": {"median": 32.4}, "latest_operating_margin_pct": {"median": 22.5}, "operating_margin_change_pp": {"median": 4.1}, "debt_to_equity": {"median": 0.15}, "borrowing_change_pct": {"median": -12.0}, "latest_ocf_to_pat": {"median": 1.5}, "pat_growth_volatility_pct": {"median": 12.3}}},
                        "pillar_scores": {"growth": {"score": 95.2}, "profitability": {"score": 88.4}, "financial_strength": {"score": 92.0}, "earnings_quality": {"score": 85.1}}
                    },
                    "macro": {"sector_score": {"llm_overall_impact": "POSITIVE", "categories": {"INTEREST_RATES_AND_LIQUIDITY": {"impact": "NEUTRAL", "numeric_value": 50, "confidence": "MEDIUM"}}}},
                    "median_relative_return": 15.4, "outperformance_breadth": 68.0, "percent_consistency_gte_60": 72.0, "positive_return_breadth": 85.0
                }
            )
            db.add(gs)
            db.add(DiscoverySelection(id=str(uuid.uuid4()), run_id=run_id, horizon=horizon, entity_type="SECTOR", entity_name=s["name"], rank=s["rank"], selected=True))

        # 4. Seed Industries
        industries = [
            {"name": "Software", "parent": "Technology", "rank": 1},
            {"name": "Hardware", "parent": "Technology", "rank": 2},
            {"name": "Biotech", "parent": "Healthcare", "rank": 3},
            {"name": "Oil & Gas", "parent": "Energy", "rank": 4}
        ]
        for i in industries:
            gs = GroupScore(
                id=str(uuid.uuid4()), run_id=run_id, entity_type="INDUSTRY", entity_name=i["name"], parent_sector=i["parent"], horizon=horizon,
                technical_score=90.2, fundamental_score=80.5, macro_score=85.0, final_score=88.4, rank=i["rank"],
                technical_return_score=95.0, technical_breadth_score=88.5, technical_volume_score=75.2, technical_consistency_score=95.0,
                calculation_details={"macro": {"industry_score": {"llm_overall_impact": "POSITIVE", "categories": {}}}, "fundamental": {"pillar_scores": {}}}
            )
            db.add(gs)
            db.add(DiscoverySelection(id=str(uuid.uuid4()), run_id=run_id, horizon=horizon, entity_type="INDUSTRY", entity_name=i["name"], parent_sector=i["parent"], rank=i["rank"], selected=True))

        # 5. Seed Basic Industries
        basic = [
            {"name": "Cloud Computing", "parent_sec": "Technology", "parent_ind": "Software", "rank": 1},
            {"name": "Cybersecurity", "parent_sec": "Technology", "parent_ind": "Software", "rank": 2},
            {"name": "Genomics", "parent_sec": "Healthcare", "parent_ind": "Biotech", "rank": 3},
            {"name": "Exploration", "parent_sec": "Energy", "parent_ind": "Oil & Gas", "rank": 4}
        ]
        for b in basic:
            gs = GroupScore(
                id=str(uuid.uuid4()), run_id=run_id, entity_type="BASIC_INDUSTRY", entity_name=b["name"], parent_sector=b["parent_sec"], parent_industry=b["parent_ind"], horizon=horizon,
                technical_score=95.2, fundamental_score=85.5, macro_score=90.0, final_score=92.4, rank=b["rank"],
                technical_return_score=98.0, technical_breadth_score=92.5, technical_volume_score=85.2, technical_consistency_score=98.0,
                calculation_details={"macro": {"basic_industry_score": {"llm_overall_impact": "POSITIVE", "categories": {}}}, "fundamental": {"pillar_scores": {}}}
            )
            db.add(gs)
            db.add(DiscoverySelection(id=str(uuid.uuid4()), run_id=run_id, horizon=horizon, entity_type="BASIC_INDUSTRY", entity_name=b["name"], parent_sector=b["parent_sec"], parent_industry=b["parent_ind"], rank=b["rank"], selected=True))

        # 6. Seed Stocks & Metrics
        stocks = [
            {"sym": "TECHSTK1", "sec": "Technology", "ind": "Software", "bas": "Cloud Computing", "rank": 1, "final": 95.0, "tech": 98.0, "fund": 92.0, "mac": 90.0, "status": "VERY_STRONG"},
            {"sym": "TECHSTK2", "sec": "Technology", "ind": "Software", "bas": "Cybersecurity", "rank": 2, "final": 88.0, "tech": 85.0, "fund": 89.0, "mac": 80.0, "status": "STRONG"},
            {"sym": "HEALTHSTK1", "sec": "Healthcare", "ind": "Biotech", "bas": "Genomics", "rank": 3, "final": 82.0, "tech": 75.0, "fund": 90.0, "mac": 80.0, "status": "STRONG"},
            {"sym": "ENERGYSTK1", "sec": "Energy", "ind": "Oil & Gas", "bas": "Exploration", "rank": 4, "final": 35.0, "tech": 30.0, "fund": 40.0, "mac": 32.0, "status": "WEAK"}
        ]
        for i, s in enumerate(stocks):
            cid = f"c{i+1}"
            
            # Stock Details
            db.add(DiscoverySelection(
                id=str(uuid.uuid4()), run_id=run_id, horizon=horizon, entity_type="STOCK", entity_name=s["sym"], symbol=s["sym"], company_id=cid,
                parent_sector=s["sec"], parent_industry=s["ind"], basic_industry=s["bas"], rank=s["rank"], selected=True
            ))
            db.add(StockCandidateSnapshot(
                id=str(uuid.uuid4()), run_id=run_id, horizon=horizon, company_id=cid, symbol=s["sym"],
                sector=s["sec"], industry=s["ind"], basic_industry=s["bas"], status="COMPLETED",
                technical_score=s["tech"], fundamental_score=s["fund"], inherited_macro_score=s["mac"], final_score=s["final"],
                score_status=s["status"], score_coverage_pct=100.0, rank=s["rank"], selected=True
            ))
            db.add(EligibleUniverseSnapshot(
                id=str(uuid.uuid4()), run_id=run_id, as_of_date=datetime.date(2026, 7, 21), horizon=horizon, source_company_id=cid, symbol=s["sym"],
                sector=s["sec"], industry=s["ind"], basic_industry=s["bas"], return_available=True, volume_available=True, consistency_available=True,
                financial_data_available=True, technical_data_coverage=100.0, fundamental_data_coverage=100.0,
                eligible_for_sector=True, eligible_for_industry=True, eligible_for_basic_industry=True, exclusion_reasons=[]
            ))
            
            # Individual Technical & Fundamental Metrics
            db.add(CompanyTechnicalMetric(
                id=str(uuid.uuid4()), run_id=run_id, source_company_id=cid, symbol=s["sym"], horizon=horizon,
                final_technical_score=s["tech"], technical_status="COMPLETE", company_return=10.5, benchmark_return=5.2,
                calculation_details={"scores": {"return_score": s["tech"]}}
            ))
            db.add(CompanyFundamentalMetric(
                id=str(uuid.uuid4()), run_id=run_id, source_company_id=cid, symbol=s["sym"],
                final_fundamental_score=s["fund"], fundamental_status="COMPLETE",
                calculation_details={"pillar_scores": {"growth": {"score": s["fund"]}}}
            ))

        db.commit()
        print(f"Database seeded with {run_id} successfully!")
    except Exception as e:
        db.rollback()
        print("Error seeding database:", e)
    finally:
        db.close()

if __name__ == "__main__":
    seed_db()
