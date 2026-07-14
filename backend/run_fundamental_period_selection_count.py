"""Run fundamental period selection and print results using mocked DB connection."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock
from services.fundamental.fundamental_period_selection import FundamentalPeriodSelectionService

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

mock_rows = [
    MockRow(
        source_company_id="comp_1", symbol="AAPL", overview_id="over_1",
        pl_periods=["Mar 2024", "Mar 2023"],
        bs_periods=["Mar 2024", "Mar 2023"],
        cf_periods=["Mar 2024", "Mar 2023"]
    ),
    MockRow(
        source_company_id="comp_2", symbol="MSFT", overview_id="over_2",
        pl_periods=["Dec 2023", "Dec 2022"],
        bs_periods=["Dec 2023"],
        cf_periods=["Dec 2023", "Dec 2022"]
    ),
    MockRow(
        source_company_id="comp_3", symbol="TSLA", overview_id="over_3",
        pl_periods=["Mar 2024", "Mar 2023", "Q1 2024"],
        bs_periods=["Mar 2024", "Mar 2023"],
        cf_periods=["Mar 2024", "Mar 2023"]
    ),
    MockRow(
        source_company_id="comp_4", symbol="META", overview_id="over_4",
        pl_periods=["Mar 2024", "Mar 2022"], # Gap > 430
        bs_periods=["Mar 2024", "Mar 2022"],
        cf_periods=["Mar 2024", "Mar 2022"]
    ),
    MockRow(
        source_company_id="comp_5", symbol="AMZN", overview_id="over_5",
        pl_periods=["Mar 2024", "Mar 2023"],
        bs_periods=["Mar 2024", "Mar 2023"],
        cf_periods=None # Missing CF
    )
]

mock_session = MagicMock()
mock_session.execute.return_value.fetchall.return_value = mock_rows

svc = FundamentalPeriodSelectionService(mock_session)
results = svc.select_periods()

count_pl_comp = sum(1 for r in results if r["profit_loss"]["comparable"])
count_bs_comp = sum(1 for r in results if r["balance_sheet"]["comparable"])
count_cf_comp = sum(1 for r in results if r["cash_flow"]["comparable"])
count_common_comp = sum(1 for r in results if r["profit_loss_cash_flow_common"]["comparable"])

print(f"\n=== Fundamental Period Selection Pipeline ===")
print(f"Total Companies Processed: {len(results)}")
print(f"Comparable P&L: {count_pl_comp}")
print(f"Comparable Balance Sheet: {count_bs_comp}")
print(f"Comparable Cash Flow: {count_cf_comp}")
print(f"Comparable Common P&L/CF: {count_common_comp}")

for r in results:
    if r["warnings"]:
        print(f" - {r['symbol']} warnings: {', '.join(r['warnings'])}")

