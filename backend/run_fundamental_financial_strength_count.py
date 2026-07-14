"""Run fundamental financial strength logic and print results using mocked DB connection."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock
from services.fundamental.fundamental_financial_strength import FundamentalFinancialStrengthService
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
                "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"},
                "warnings": []
            },
            {
                "source_company_id": "comp_2", "symbol": "BANK", "overview_id": "over_2",
                "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"},
                "warnings": []
            }
        ]

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

bs_records = [
    MockRow(company_id="over_1", period="P", equity_capital=100.0, reserves=100.0, borrowings=200.0),
    MockRow(company_id="over_1", period="L", equity_capital=100.0, reserves=300.0, borrowings=100.0),
    MockRow(company_id="over_2", period="P", equity_capital=100.0, reserves=100.0, borrowings=500.0),
    MockRow(company_id="over_2", period="L", equity_capital=100.0, reserves=100.0, borrowings=600.0),
]

h_records = [
    MockRow(id="comp_1", share_symbol="AAPL", sectore="Tech", industry="Hard", categorized_industry="Phones"),
    MockRow(id="comp_2", share_symbol="BANK", sectore="Fin", industry="Private Sector Bank", categorized_industry=None),
]

def mock_execute(query, params=None):
    query_str = str(query)
    if "company_balance_sheets" in query_str:
        return MagicMock(fetchall=lambda: bs_records)
    elif "FROM companies" in query_str:
        return MagicMock(fetchall=lambda: h_records)
    return MagicMock(fetchall=lambda: [])

mock_src = MagicMock()
mock_src.execute.side_effect = mock_execute

svc = FundamentalFinancialStrengthService(mock_src, disc)
svc._period_svc = MockPeriodSvc()
svc.calculate_financial_strength(run_id)

results = disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
count_excluded = sum(1 for r in results if r.calculation_details["financial_strength"]["business_classification"] == "EXCLUDED_FINANCIAL")
count_dte = sum(1 for r in results if r.calculation_details["financial_strength"]["debt_to_equity_available"])
count_trend = sum(1 for r in results if r.calculation_details["financial_strength"]["borrowing_trend_available"])

print(f"\n=== Fundamental Financial Strength Pipeline ===")
print(f"Metrics Scored: {len(results)}")
print(f"Excluded Financial Businesses: {count_excluded}")
print(f"Debt-to-Equity Available: {count_dte}")
print(f"Borrowing Trend Available: {count_trend}")

disc.close()
