"""
Tests for FundamentalProfitabilityService.
"""
import uuid
import pytest
from unittest.mock import MagicMock
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_profitability import FundamentalProfitabilityService

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


def test_fundamental_profitability_logic(disc_session):
    selections = [
        # c1: AAPL -> Positive margins, Strong Expansion
        {"source_company_id": "c1", "symbol": "AAPL", "overview_id": "o1", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c2: MSFT -> Negative latest margin, Normal Expansion
        {"source_company_id": "c2", "symbol": "MSFT", "overview_id": "o2", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c3: TSLA -> Negative previous margin, Stable
        {"source_company_id": "c3", "symbol": "TSLA", "overview_id": "o3", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c4: AMZN -> Normal contraction
        {"source_company_id": "c4", "symbol": "AMZN", "overview_id": "o4", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c5: META -> Strong contraction
        {"source_company_id": "c5", "symbol": "META", "overview_id": "o5", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c6: NFLX -> Zero latest sales
        {"source_company_id": "c6", "symbol": "NFLX", "overview_id": "o6", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c7: GOOG -> Negative previous sales
        {"source_company_id": "c7", "symbol": "GOOG", "overview_id": "o7", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c8: NVDA -> Missing latest OP
        {"source_company_id": "c8", "symbol": "NVDA", "overview_id": "o8", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c9: INTC -> Missing previous OP
        {"source_company_id": "c9", "symbol": "INTC", "overview_id": "o9", "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        # c10: AMD -> Non-comparable P&L
        {"source_company_id": "c10", "symbol": "AMD", "overview_id": "o10", "profit_loss": {"comparable": False, "latest_period": "L", "previous_period": "P"}, "warnings": []},
    ]

    pl_records = [
        # AAPL (1, 3, 5): p_sales=100, p_op=10 (10%), l_sales=100, l_op=15 (15%) -> Change: 5.0 (STRONG_EXPANSION)
        MockRow(company_id="o1", period="P", sales=100.0, operating_profit=10.0),
        MockRow(company_id="o1", period="L", sales=100.0, operating_profit=15.0),
        
        # MSFT (2, 6): p_sales=100, p_op=-10 (-10%), l_sales=100, l_op=-8 (-8%) -> Change: 2.0 (EXPANSION)
        MockRow(company_id="o2", period="P", sales=100.0, operating_profit=-10.0),
        MockRow(company_id="o2", period="L", sales=100.0, operating_profit=-8.0),
        
        # TSLA (4, 7): p_sales=100, p_op=-10 (-10%), l_sales=100, l_op=-10 (-10%) -> Change: 0.0 (STABLE)
        MockRow(company_id="o3", period="P", sales=100.0, operating_profit=-10.0),
        MockRow(company_id="o3", period="L", sales=100.0, operating_profit=-10.0),
        
        # AMZN (8): p_sales=100, p_op=10 (10%), l_sales=100, l_op=8 (8%) -> Change: -2.0 (CONTRACTION)
        MockRow(company_id="o4", period="P", sales=100.0, operating_profit=10.0),
        MockRow(company_id="o4", period="L", sales=100.0, operating_profit=8.0),
        
        # META (9): p_sales=100, p_op=10 (10%), l_sales=100, l_op=5 (5%) -> Change: -5.0 (STRONG_CONTRACTION)
        MockRow(company_id="o5", period="P", sales=100.0, operating_profit=10.0),
        MockRow(company_id="o5", period="L", sales=100.0, operating_profit=5.0),
        
        # NFLX (10): p_sales=100, p_op=10, l_sales=0, l_op=10 -> l_margin invalid
        MockRow(company_id="o6", period="P", sales=100.0, operating_profit=10.0),
        MockRow(company_id="o6", period="L", sales=0.0, operating_profit=10.0),
        
        # GOOG (11): p_sales=-100, p_op=10, l_sales=100, l_op=10 -> p_margin invalid
        MockRow(company_id="o7", period="P", sales=-100.0, operating_profit=10.0),
        MockRow(company_id="o7", period="L", sales=100.0, operating_profit=10.0),
        
        # NVDA (12): missing latest OP
        MockRow(company_id="o8", period="P", sales=100.0, operating_profit=10.0),
        MockRow(company_id="o8", period="L", sales=100.0, operating_profit=None),
        
        # INTC (13, 14): missing previous OP -> Latest margin available, Trend unavailable
        MockRow(company_id="o9", period="P", sales=100.0, operating_profit=None),
        MockRow(company_id="o9", period="L", sales=100.0, operating_profit=10.0),
    ]

    h_records = [
        MockRow(id="c1", share_symbol="AAPL", sectore="Tech", industry="Hard", categorized_industry="Phones"),
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

    svc = FundamentalProfitabilityService(mock_src, disc_session)
    svc._period_svc = MockPeriodSvc(selections)
    
    # 16, 17. Existing fields remain unchanged, JSON details merged
    existing = CompanyFundamentalMetric(
        id=str(uuid.uuid4()), run_id="run1", source_company_id="c1", symbol="AAPL",
        growth_score=99.0, calculation_details={"growth": {"x": 1}, "warnings": ["EXISTING_WARNING"]}
    )
    disc_session.add(existing)
    disc_session.commit()

    svc.calculate_profitability("run1")

    # AAPL
    aapl = _get_metric(disc_session, "AAPL")
    assert aapl.growth_score == 99.0 # Preserved
    assert aapl.calculation_details["growth"]["x"] == 1 # Preserved JSON details
    assert "EXISTING_WARNING" in aapl.calculation_details["warnings"] # Warnings merged safely
    
    p = aapl.calculation_details["profitability"]
    assert p["latest_operating_margin_pct"] == 15.0 # 1. Pos latest margin
    assert p["previous_operating_margin_pct"] == 10.0 # 3. Pos previous margin
    assert p["operating_margin_change_pp"] == 5.0 
    assert p["margin_trend_status"] == "STRONG_EXPANSION" # 5. Strong margin expansion
    assert p["latest_operating_margin_available"] is True
    assert p["profitability_available"] is True

    # MSFT
    msft = _get_metric(disc_session, "MSFT")
    p = msft.calculation_details["profitability"]
    assert p["latest_operating_margin_pct"] == -8.0 # 2. Neg latest margin
    assert p["operating_margin_change_pp"] == 2.0 
    assert p["margin_trend_status"] == "EXPANSION" # 6. Normal expansion

    # TSLA
    tsla = _get_metric(disc_session, "TSLA")
    p = tsla.calculation_details["profitability"]
    assert p["previous_operating_margin_pct"] == -10.0 # 4. Neg previous margin
    assert p["operating_margin_change_pp"] == 0.0
    assert p["margin_trend_status"] == "STABLE" # 7. Stable margin

    # AMZN
    amzn = _get_metric(disc_session, "AMZN")
    assert amzn.calculation_details["profitability"]["margin_trend_status"] == "CONTRACTION" # 8. Normal contraction
    
    # META
    meta = _get_metric(disc_session, "META")
    assert meta.calculation_details["profitability"]["margin_trend_status"] == "STRONG_CONTRACTION" # 9. Strong contraction

    # NFLX
    nflx = _get_metric(disc_session, "NFLX")
    assert nflx.calculation_details["profitability"]["latest_operating_margin_pct"] is None # 10. Zero latest sales
    assert "INVALID_LATEST_SALES_BASE" in nflx.calculation_details["warnings"]

    # GOOG
    goog = _get_metric(disc_session, "GOOG")
    assert goog.calculation_details["profitability"]["previous_operating_margin_pct"] is None # 11. Negative previous sales
    assert "INVALID_PREVIOUS_SALES_BASE" in goog.calculation_details["warnings"]

    # NVDA
    nvda = _get_metric(disc_session, "NVDA")
    assert "MISSING_LATEST_OPERATING_PROFIT" in nvda.calculation_details["warnings"] # 12. Missing latest OP
    
    # INTC
    intc = _get_metric(disc_session, "INTC")
    assert "MISSING_PREVIOUS_OPERATING_PROFIT" in intc.calculation_details["warnings"] # 13. Missing prev OP
    assert intc.calculation_details["profitability"]["latest_operating_margin_available"] is True # 14. Latest margin remains available
    assert intc.calculation_details["profitability"]["operating_margin_trend_available"] is False
    assert "OPERATING_MARGIN_TREND_UNAVAILABLE" in intc.calculation_details["warnings"]
    
    # AMD
    amd = _get_metric(disc_session, "AMD")
    assert "INSUFFICIENT_PROFIT_LOSS_PERIODS" in amd.calculation_details["warnings"] # 15. Non-comparable P&L

    # 18. Idempotent update
    svc.calculate_profitability("run1")
    aapl_2 = _get_metric(disc_session, "AAPL")
    assert aapl_2.calculation_details["profitability"]["operating_margin_change_pp"] == 5.0
    
    # 19. Bulk query implicit
    # 20. Source DB is read-only (mocked execute on source has no writes)
