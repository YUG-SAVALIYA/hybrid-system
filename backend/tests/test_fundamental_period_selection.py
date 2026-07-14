"""
Tests for FundamentalPeriodSelectionService.
"""
import pytest
from unittest.mock import MagicMock
from services.fundamental.fundamental_period_selection import FundamentalPeriodSelectionService

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ------------------------------------------------------------------ #
#  1-6, 11-14. Bridge, Standard Periods, Common Periods, Isolations    #
# ------------------------------------------------------------------ #

def test_standard_periods_and_bridge():
    # 1. Correct bridge
    aapl_row = MockRow(
        source_company_id="comp_AAPL",
        symbol="AAPL",
        overview_id="over_AAPL",
        pl_periods=["Mar 2024", "Mar 2023"],
        bs_periods=None, # 11. Missing BS does not invalidate P&L
        cf_periods=["Mar 2024", "Mar 2023"]
    )
    
    # 12. Missing overview warning
    msft_row = MockRow(
        source_company_id="comp_MSFT",
        symbol="MSFT",
        overview_id=None,
        pl_periods=None, bs_periods=None, cf_periods=None
    )
    
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [aapl_row, msft_row]
    
    svc = FundamentalPeriodSelectionService(mock_session)
    res = svc.select_periods()
    
    aapl = [r for r in res if r["symbol"] == "AAPL"][0]
    msft = [r for r in res if r["symbol"] == "MSFT"][0]
    
    assert aapl["source_company_id"] == "comp_AAPL"
    assert aapl["overview_id"] == "over_AAPL"
    
    assert msft["overview_id"] is None
    assert "MISSING_COMPANY_OVERVIEW" in msft["warnings"]
    
    # 2. Latest and previous P&L
    assert aapl["profit_loss"]["latest_period"] == "Mar 2024"
    assert aapl["profit_loss"]["previous_period"] == "Mar 2023"
    assert aapl["profit_loss"]["comparable"] is True
    
    # 3 & 11. Missing BS -> no comparable
    assert aapl["balance_sheet"]["comparable"] is False
    assert "INSUFFICIENT_BALANCE_SHEET_PERIODS" in aapl["warnings"]
    
    # 4. CF periods
    assert aapl["cash_flow"]["latest_period"] == "Mar 2024"
    assert aapl["cash_flow"]["comparable"] is True
    
    # 5-6. Latest and previous common
    assert aapl["profit_loss_cash_flow_common"]["latest_period"] == "Mar 2024"
    assert aapl["profit_loss_cash_flow_common"]["previous_period"] == "Mar 2023"
    assert aapl["profit_loss_cash_flow_common"]["comparable"] is True


# ------------------------------------------------------------------ #
#  7-10. Quarterly, Ambiguous, Duplicate, Non-consecutive            #
# ------------------------------------------------------------------ #

def test_period_exclusions_and_gaps():
    tsla_row = MockRow(
        source_company_id="comp_TSLA", symbol="TSLA", overview_id="over_TSLA",
        pl_periods=["Q1 2024", "Mar 2024", "Mar 2023"], # 7. Quarterly excluded
        bs_periods=None, cf_periods=None
    )
    
    meta_row = MockRow(
        source_company_id="comp_META", symbol="META", overview_id="over_META",
        pl_periods=["Mar 2024", "Dec 2023"], # 8. Ambiguous periods
        bs_periods=None, cf_periods=None
    )
    
    nflx_row = MockRow(
        source_company_id="comp_NFLX", symbol="NFLX", overview_id="over_NFLX",
        pl_periods=["Mar 2024", "Mar 2024"], # 9. Duplicate calendar year
        bs_periods=None, cf_periods=None
    )
    
    amzn_row = MockRow(
        source_company_id="comp_AMZN", symbol="AMZN", overview_id="over_AMZN",
        pl_periods=["Mar 2024", "Mar 2022"], # 10. Non-consecutive
        bs_periods=None, cf_periods=None
    )
    
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [tsla_row, meta_row, nflx_row, amzn_row]
    
    svc = FundamentalPeriodSelectionService(mock_session)
    res = svc.select_periods()
    
    tsla = [r for r in res if r["symbol"] == "TSLA"][0]
    meta = [r for r in res if r["symbol"] == "META"][0]
    nflx = [r for r in res if r["symbol"] == "NFLX"][0]
    amzn = [r for r in res if r["symbol"] == "AMZN"][0]
    
    # TSLA: Q1 ignored, still has Mar 2024 / Mar 2023
    assert tsla["profit_loss"]["latest_period"] == "Mar 2024"
    assert tsla["profit_loss"]["comparable"] is True
    
    # META: Mixed months -> AMBIGUOUS -> excluded -> not comparable
    assert meta["profit_loss"]["comparable"] is False
    assert "INSUFFICIENT_PROFIT_LOSS_PERIODS" in meta["warnings"]
    
    # NFLX: Duplicate year -> DUPLICATE -> excluded -> not comparable
    assert nflx["profit_loss"]["comparable"] is False
    assert "INSUFFICIENT_PROFIT_LOSS_PERIODS" in nflx["warnings"]
    
    # AMZN: Non-consecutive -> gap > 430 -> rejected
    assert amzn["profit_loss"]["latest_period"] == "Mar 2024"
    assert amzn["profit_loss"]["previous_period"] == "Mar 2022"
    assert amzn["profit_loss"]["comparable"] is False
    assert "NON_CONSECUTIVE_ANNUAL_PERIODS" in amzn["warnings"]
