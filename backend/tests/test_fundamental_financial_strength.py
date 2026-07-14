"""
Tests for FundamentalFinancialStrengthService.
"""
import uuid
import pytest
from unittest.mock import MagicMock
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_financial_strength import FundamentalFinancialStrengthService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    session.close()

def _get_metric(session, symbol):
    return session.query(CompanyFundamentalMetric).filter_by(symbol=symbol).first()

class MockPeriodSvc:
    def __init__(self, selections):
        self.selections = selections
    def select_periods(self):
        return self.selections

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_fundamental_financial_strength_logic(disc_session):
    selections = [
        {"source_company_id": "c1", "symbol": "AAPL", "overview_id": "o1", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c2", "symbol": "MSFT", "overview_id": "o2", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c3", "symbol": "TSLA", "overview_id": "o3", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c4", "symbol": "AMZN", "overview_id": "o4", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c5", "symbol": "META", "overview_id": "o5", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c6", "symbol": "NFLX", "overview_id": "o6", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c7", "symbol": "GOOG", "overview_id": "o7", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c8", "symbol": "NVDA", "overview_id": "o8", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c9", "symbol": "BANK", "overview_id": "o9", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c10", "symbol": "NBFC", "overview_id": "o10", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c11", "symbol": "HFC", "overview_id": "o11", "balance_sheet": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c12", "symbol": "AMD", "overview_id": "o12", "balance_sheet": {"comparable": False, "latest_period": "L", "previous_period": "P"}, "warnings": []},
    ]

    bs_records = [
        # AAPL: Standard company, positive DTE, borrowings decrease
        MockRow(company_id="o1", period="P", equity_capital=100.0, reserves=100.0, borrowings=200.0),
        MockRow(company_id="o1", period="L", equity_capital=100.0, reserves=300.0, borrowings=100.0), # eq = 400, dte = 0.25
        
        # MSFT: Zero borrowings -> DTE zero, unchanged
        MockRow(company_id="o2", period="P", equity_capital=100.0, reserves=100.0, borrowings=0.0),
        MockRow(company_id="o2", period="L", equity_capital=100.0, reserves=100.0, borrowings=0.0),
        
        # TSLA: Zero equity (ec=100, res=-100), missing prev borrowings
        MockRow(company_id="o3", period="P", equity_capital=100.0, reserves=100.0, borrowings=None),
        MockRow(company_id="o3", period="L", equity_capital=100.0, reserves=-100.0, borrowings=100.0),
        
        # AMZN: Negative equity, zero to positive borrowings
        MockRow(company_id="o4", period="P", equity_capital=100.0, reserves=100.0, borrowings=0.0),
        MockRow(company_id="o4", period="L", equity_capital=100.0, reserves=-200.0, borrowings=100.0),
        
        # META: Missing latest EC, borrowings increase
        MockRow(company_id="o5", period="P", equity_capital=100.0, reserves=100.0, borrowings=100.0),
        MockRow(company_id="o5", period="L", equity_capital=None, reserves=100.0, borrowings=200.0),
        
        # NFLX: Missing reserves
        MockRow(company_id="o6", period="P", equity_capital=100.0, reserves=100.0, borrowings=100.0),
        MockRow(company_id="o6", period="L", equity_capital=100.0, reserves=None, borrowings=200.0),
        
        # GOOG: Negative borrowings
        MockRow(company_id="o7", period="P", equity_capital=100.0, reserves=100.0, borrowings=100.0),
        MockRow(company_id="o7", period="L", equity_capital=100.0, reserves=100.0, borrowings=-50.0),
        
        # NVDA: Missing latest borrowings
        MockRow(company_id="o8", period="P", equity_capital=100.0, reserves=100.0, borrowings=100.0),
        MockRow(company_id="o8", period="L", equity_capital=100.0, reserves=100.0, borrowings=None),
        
        # Financial companies: healthy equity/borrowings, should just skip debt logic
        MockRow(company_id="o9", period="P", equity_capital=100.0, reserves=100.0, borrowings=100.0),
        MockRow(company_id="o9", period="L", equity_capital=100.0, reserves=100.0, borrowings=200.0),
        MockRow(company_id="o10", period="P", equity_capital=100.0, reserves=100.0, borrowings=100.0),
        MockRow(company_id="o10", period="L", equity_capital=100.0, reserves=100.0, borrowings=200.0),
        MockRow(company_id="o11", period="P", equity_capital=100.0, reserves=100.0, borrowings=100.0),
        MockRow(company_id="o11", period="L", equity_capital=100.0, reserves=100.0, borrowings=200.0),
    ]

    h_records = [
        MockRow(id="c1", share_symbol="AAPL", sectore="Tech", industry="Hard", categorized_industry="Phones"),
        MockRow(id="c9", share_symbol="BANK", sectore="Fin", industry="Private Sector Bank", categorized_industry=None),
        MockRow(id="c10", share_symbol="NBFC", sectore="Fin", industry="Non Banking Financial Company (NBFC)", categorized_industry=None),
        MockRow(id="c11", share_symbol="HFC", sectore="Fin", industry="Housing Finance Company", categorized_industry=None),
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

    svc = FundamentalFinancialStrengthService(mock_src, disc_session)
    svc._period_svc = MockPeriodSvc(selections)
    
    existing = CompanyFundamentalMetric(
        id=str(uuid.uuid4()), run_id="run1", source_company_id="c1", symbol="AAPL",
        growth_score=99.0, calculation_details={"growth": {"x": 1}, "warnings": ["EXISTING_WARNING"]}
    )
    disc_session.add(existing)
    disc_session.commit()

    svc.calculate_financial_strength("run1")

    # AAPL
    aapl = _get_metric(disc_session, "AAPL")
    assert aapl.growth_score == 99.0 # Preserved
    fs = aapl.calculation_details["financial_strength"]
    assert fs["business_classification"] == "STANDARD_NON_FINANCIAL" # 17. Standard company
    assert fs["latest"]["equity"] == 400.0 # 1. Equity calculation
    assert fs["debt_to_equity"] == 0.25 # 3. Positive DTE
    assert fs["borrowing_transition"] == "DECREASED" # 9. Borrowings decrease
    assert fs["financial_strength_available"] is True

    # MSFT
    msft = _get_metric(disc_session, "MSFT")
    fs = msft.calculation_details["financial_strength"]
    assert fs["debt_to_equity"] == 0.0 # 2. Zero borrowings -> DTE zero
    assert fs["borrowing_transition"] == "ZERO_TO_ZERO" # 12. Zero borrowings remain zero
    assert "BORROWING_PERCENTAGE_CHANGE_UNAVAILABLE" in msft.calculation_details["warnings"]

    # TSLA
    tsla = _get_metric(disc_session, "TSLA")
    fs = tsla.calculation_details["financial_strength"]
    assert fs["latest"]["equity"] == 0.0 # 4. Zero equity
    assert fs["debt_to_equity"] is None
    assert "NON_POSITIVE_LATEST_EQUITY" in tsla.calculation_details["warnings"]
    assert "MISSING_PREVIOUS_BORROWINGS" in tsla.calculation_details["warnings"] # 18. Missing previous borrowings preserves latest DTE
    assert fs["financial_strength_available"] is True # Equity is available (0 is available) and borrowings available

    # AMZN
    amzn = _get_metric(disc_session, "AMZN")
    fs = amzn.calculation_details["financial_strength"]
    assert fs["latest"]["equity"] == -100.0 # 5. Negative equity
    assert "NON_POSITIVE_LATEST_EQUITY" in amzn.calculation_details["warnings"]
    assert fs["borrowing_transition"] == "ZERO_TO_POSITIVE" # 11. Zero borrowings become positive

    # META & NFLX
    meta = _get_metric(disc_session, "META")
    assert "MISSING_LATEST_EQUITY_CAPITAL" in meta.calculation_details["warnings"] # 6. Missing EC
    nflx = _get_metric(disc_session, "NFLX")
    assert "MISSING_LATEST_RESERVES" in nflx.calculation_details["warnings"] # 7. Missing reserves

    # GOOG
    goog = _get_metric(disc_session, "GOOG")
    fs = goog.calculation_details["financial_strength"]
    assert "INVALID_NEGATIVE_BORROWINGS" in goog.calculation_details["warnings"] # 13. Negative borrowings
    assert fs["borrowing_transition"] == "INVALID_NEGATIVE_BORROWINGS"
    assert fs["financial_strength_available"] is False

    # NVDA
    nvda = _get_metric(disc_session, "NVDA")
    assert "MISSING_LATEST_BORROWINGS" in nvda.calculation_details["warnings"]

    # BANK, NBFC, HFC
    for sym in ["BANK", "NBFC", "HFC"]:
        m = _get_metric(disc_session, sym)
        fs = m.calculation_details["financial_strength"]
        assert fs["business_classification"] == "EXCLUDED_FINANCIAL" # 14, 15, 16.
        assert fs["standard_debt_rule_applicable"] is False
        assert fs["debt_to_equity"] is None
        assert "STANDARD_DEBT_RULE_NOT_APPLICABLE" in m.calculation_details["warnings"]
        assert fs["borrowing_transition"] == "INCREASED" # 8. Borrowings increase
        assert fs["financial_strength_available"] is True # True even if DTE not applicable

    # AMD
    amd = _get_metric(disc_session, "AMD")
    assert "INSUFFICIENT_BALANCE_SHEET_PERIODS" in amd.calculation_details["warnings"] # 19. Non-comparable BS

    # 22. Idempotent update
    svc.calculate_financial_strength("run1")
    aapl_2 = _get_metric(disc_session, "AAPL")
    assert aapl_2.calculation_details["financial_strength"]["borrowing_transition"] == "DECREASED"
