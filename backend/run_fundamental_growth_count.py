"""Run fundamental growth logic and print results using mocked DB connection."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock
from services.fundamental.fundamental_growth import FundamentalGrowthService
from database import DiscoverySessionLocal
from sqlalchemy import text
from models.discovery import CompanyFundamentalMetric
import uuid

disc = DiscoverySessionLocal()
run_id = f"test_run_{uuid.uuid4().hex[:8]}"

class MockPeriodSvc:
    def select_periods(self):
        return [
            {
                "source_company_id": "comp_1", "symbol": "AAPL", "overview_id": "over_1",
                "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
                "warnings": []
            },
            {
                "source_company_id": "comp_2", "symbol": "MSFT", "overview_id": "over_2",
                "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
                "warnings": []
            }
        ]

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

pl_records = [
    MockRow(company_id="over_1", period="P", sales=100.0, net_profit=10.0),
    MockRow(company_id="over_1", period="L", sales=150.0, net_profit=20.0),
    MockRow(company_id="over_2", period="P", sales=0.0, net_profit=-10.0),
    MockRow(company_id="over_2", period="L", sales=100.0, net_profit=10.0),
]

h_records = [
    MockRow(id="comp_1", share_symbol="AAPL", sectore="Tech", industry="Hard", categorized_industry="Phones"),
    MockRow(id="comp_2", share_symbol="MSFT", sectore="Tech", industry="Soft", categorized_industry="OS"),
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

svc = FundamentalGrowthService(mock_src, disc)
svc._period_svc = MockPeriodSvc()
svc.calculate_growth(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
count_sales = sum(1 for r in results if r.calculation_details["growth"]["sales_growth_available"])
count_np = sum(1 for r in results if r.calculation_details["growth"]["net_profit_growth_available"])
count_overall = sum(1 for r in results if r.calculation_details["growth"]["growth_available"])

print(f"\n=== Fundamental Growth Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Sales Growth Available: {count_sales}")
print(f"Net Profit Growth Available: {count_np}")
print(f"Overall Growth Available: {count_overall}")

disc.close()
