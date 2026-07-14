"""Run fundamental profit stability logic and print results using mocked DB connection."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock
from services.fundamental.fundamental_profit_stability import FundamentalProfitStabilityService
from database import DiscoverySessionLocal
from sqlalchemy import text
from models.discovery import CompanyFundamentalMetric
import uuid

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

h_records = [
    MockRow(source_company_id="c1", symbol="C1", overview_id="o1", sector="Tech", industry="Hard", basic_industry="Phones"),
    MockRow(source_company_id="c2", symbol="C2", overview_id="o2", sector="Tech", industry="Hard", basic_industry="Phones"),
]

pl_records = [
    MockRow(company_id="o1", period="Mar 2023", net_profit=100.0),
    MockRow(company_id="o1", period="Mar 2024", net_profit=120.0),
    MockRow(company_id="o1", period="Mar 2025", net_profit=150.0),
    MockRow(company_id="o2", period="Mar 2024", net_profit=120.0),
    MockRow(company_id="o2", period="Mar 2025", net_profit=150.0),
]

def mock_execute(query, params=None):
    query_str = str(query)
    if "company_profit_losses" in query_str:
        return MagicMock(fetchall=lambda: pl_records)
    elif "FROM companies" in query_str:
        return MagicMock(fetchall=lambda: h_records)
    return MagicMock(fetchall=lambda: [])

mock_src = MagicMock()
mock_src.execute.side_effect = mock_execute

svc = FundamentalProfitStabilityService(mock_src, disc)
svc.calculate_profit_stability(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
count_stability = sum(1 for r in results if r.calculation_details["earnings_quality"]["profit_stability"]["profit_stability_available"])
count_volatility = sum(1 for r in results if r.calculation_details["earnings_quality"]["profit_stability"]["pat_growth_volatility_available"])

print(f"\n=== Fundamental Profit Stability Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Profit Stability Available: {count_stability}")
print(f"Volatility Available: {count_volatility}")

disc.close()
