"""Run fundamental cash conversion logic and print results using mocked DB connection."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock
from services.fundamental.fundamental_cash_conversion import FundamentalCashConversionService
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
                "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"},
                "warnings": []
            },
            {
                "source_company_id": "comp_2", "symbol": "MSFT", "overview_id": "over_2",
                "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"},
                "warnings": []
            }
        ]

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

pl_records = [
    MockRow(company_id="over_1", period="P", net_profit=100.0),
    MockRow(company_id="over_1", period="L", net_profit=100.0),
    MockRow(company_id="over_2", period="P", net_profit=100.0),
    MockRow(company_id="over_2", period="L", net_profit=-100.0),
]

cf_records = [
    MockRow(company_id="over_1", period="P", cash_from_operating_activity=140.0),
    MockRow(company_id="over_1", period="L", cash_from_operating_activity=120.0),
    MockRow(company_id="over_2", period="P", cash_from_operating_activity=80.0),
    MockRow(company_id="over_2", period="L", cash_from_operating_activity=10.0),
]

h_records = [
    MockRow(id="comp_1", share_symbol="AAPL", sectore="Tech", industry="Hard", categorized_industry="Phones"),
    MockRow(id="comp_2", share_symbol="MSFT", sectore="Tech", industry="Soft", categorized_industry="OS"),
]

def mock_execute(query, params=None):
    query_str = str(query)
    if "company_profit_losses" in query_str:
        return MagicMock(fetchall=lambda: pl_records)
    elif "company_cash_flows" in query_str:
        return MagicMock(fetchall=lambda: cf_records)
    elif "FROM companies" in query_str:
        return MagicMock(fetchall=lambda: h_records)
    return MagicMock(fetchall=lambda: [])

mock_src = MagicMock()
mock_src.execute.side_effect = mock_execute

svc = FundamentalCashConversionService(mock_src, disc)
svc._period_svc = MockPeriodSvc()
svc.calculate_cash_conversion(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
count_latest = sum(1 for r in results if r.calculation_details["earnings_quality"]["cash_conversion"]["latest_cash_conversion_available"])
count_trend = sum(1 for r in results if r.calculation_details["earnings_quality"]["cash_conversion"]["cash_conversion_trend_available"])

print(f"\n=== Fundamental Cash Conversion Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Latest Ratio Available: {count_latest}")
print(f"Ratio Trend Available: {count_trend}")

disc.close()
