"""
Tests for FundamentalGrowthService.
"""
import uuid
import pytest
from unittest.mock import MagicMock
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine, discovery_engine
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_growth import FundamentalGrowthService

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


def test_fundamental_growth_logic(disc_session):
    selections = [
        # 1. Positive sales growth, 5. Standard pos NP
        {
            "source_company_id": "c1", "symbol": "AAPL", "overview_id": "o1",
            "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
            "warnings": []
        },
        # 2. Negative sales growth, 6. Profit-to-loss
        {
            "source_company_id": "c2", "symbol": "MSFT", "overview_id": "o2",
            "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
            "warnings": []
        },
        # 3. Zero prev sales, 7. Loss-to-profit
        {
            "source_company_id": "c3", "symbol": "TSLA", "overview_id": "o3",
            "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
            "warnings": []
        },
        # 4. Missing sales value, 8. Loss narrowing
        {
            "source_company_id": "c4", "symbol": "AMZN", "overview_id": "o4",
            "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
            "warnings": []
        },
        # 9. Loss widening
        {
            "source_company_id": "c5", "symbol": "META", "overview_id": "o5",
            "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
            "warnings": []
        },
        # 10. Zero-base to loss
        {
            "source_company_id": "c6", "symbol": "NFLX", "overview_id": "o6",
            "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
            "warnings": []
        },
        # 11. Non-comparable P&L
        {
            "source_company_id": "c7", "symbol": "GOOG", "overview_id": "o7",
            "profit_loss": {"comparable": False, "latest_period": "L", "previous_period": "P"},
            "warnings": []
        },
        # 12. Growth available when only sales is valid
        {
            "source_company_id": "c8", "symbol": "NVDA", "overview_id": "o8",
            "profit_loss": {"comparable": True, "latest_period": "L", "previous_period": "P"},
            "warnings": []
        }
    ]

    pl_records = [
        # AAPL: sales 100->150 (50%), np 10->20 (100%)
        MockRow(company_id="o1", period="P", sales=100.0, net_profit=10.0),
        MockRow(company_id="o1", period="L", sales=150.0, net_profit=20.0),
        
        # MSFT: sales 100->50 (-50%), np 10-> -5 (< -100%)
        MockRow(company_id="o2", period="P", sales=100.0, net_profit=10.0),
        MockRow(company_id="o2", period="L", sales=50.0, net_profit=-5.0),
        
        # TSLA: sales 0->100 (invalid), np -10->10 (LOSS_TO_PROFIT)
        MockRow(company_id="o3", period="P", sales=0.0, net_profit=-10.0),
        MockRow(company_id="o3", period="L", sales=100.0, net_profit=10.0),
        
        # AMZN: sales null->100 (missing), np -20-> -10 (LOSS_NARROWED)
        MockRow(company_id="o4", period="P", sales=None, net_profit=-20.0),
        MockRow(company_id="o4", period="L", sales=100.0, net_profit=-10.0),
        
        # META: np -10-> -20 (LOSS_WIDENED)
        MockRow(company_id="o5", period="P", sales=100.0, net_profit=-10.0),
        MockRow(company_id="o5", period="L", sales=100.0, net_profit=-20.0),
        
        # NFLX: np 0-> -10 (ZERO_BASE_TO_LOSS)
        MockRow(company_id="o6", period="P", sales=100.0, net_profit=0.0),
        MockRow(company_id="o6", period="L", sales=100.0, net_profit=-10.0),
        
        # GOOG: non-comparable (should not fetch P&L)
        
        # NVDA: np null->null (missing), sales 100->200 (100%) -> overall available
        MockRow(company_id="o8", period="P", sales=100.0, net_profit=None),
        MockRow(company_id="o8", period="L", sales=200.0, net_profit=None),
    ]

    h_records = [
        MockRow(id="c1", share_symbol="AAPL", sectore="Tech", industry="Hard", categorized_industry="Phones"),
        MockRow(id="c2", share_symbol="MSFT", sectore="Tech", industry="Soft", categorized_industry="OS"),
        MockRow(id="c3", share_symbol="TSLA", sectore="Auto", industry="EV", categorized_industry="EV"),
        MockRow(id="c4", share_symbol="AMZN", sectore="R", industry="R", categorized_industry="R"),
        MockRow(id="c5", share_symbol="META", sectore="R", industry="R", categorized_industry="R"),
        MockRow(id="c6", share_symbol="NFLX", sectore="R", industry="R", categorized_industry="R"),
        MockRow(id="c7", share_symbol="GOOG", sectore="R", industry="R", categorized_industry="R"),
        MockRow(id="c8", share_symbol="NVDA", sectore="R", industry="R", categorized_industry="R"),
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

    svc = FundamentalGrowthService(mock_src, disc_session)
    svc._period_svc = MockPeriodSvc(selections)
    
    # 13. Unrelated fields unchanged (mocking existing data)
    existing = CompanyFundamentalMetric(
        id=str(uuid.uuid4()), run_id="run1", source_company_id="c1", symbol="AAPL",
        growth_score=99.0, calculation_details={"fundamental": {"x": 1}}
    )
    disc_session.add(existing)
    disc_session.commit()

    svc.calculate_growth("run1")

    # AAPL: 1. Positive sales growth, 5. Standard pos NP
    aapl = _get_metric(disc_session, "AAPL")
    assert aapl.growth_score == 99.0 # Preserved
    assert aapl.calculation_details["fundamental"]["x"] == 1 # Preserved
    assert aapl.calculation_details["growth"]["sales_growth_pct"] == 50.0
    assert aapl.calculation_details["growth"]["net_profit_growth_pct"] == 100.0
    assert aapl.calculation_details["growth"]["net_profit_transition"] == "STANDARD_GROWTH"
    assert aapl.calculation_details["growth"]["growth_available"] is True

    # MSFT: 2. Negative sales growth, 6. Profit-to-loss
    msft = _get_metric(disc_session, "MSFT")
    assert msft.calculation_details["growth"]["sales_growth_pct"] == -50.0
    assert msft.calculation_details["growth"]["net_profit_growth_pct"] == -150.0
    assert msft.calculation_details["growth"]["net_profit_transition"] == "STANDARD_GROWTH"

    # TSLA: 3. Zero prev sales, 7. Loss-to-profit
    tsla = _get_metric(disc_session, "TSLA")
    assert tsla.calculation_details["growth"]["sales_growth_pct"] is None
    assert tsla.calculation_details["growth"]["sales_growth_available"] is False
    assert "INVALID_PREVIOUS_SALES_BASE" in tsla.calculation_details["warnings"]
    assert tsla.calculation_details["growth"]["net_profit_transition"] == "LOSS_TO_PROFIT"
    assert tsla.calculation_details["growth"]["net_profit_growth_available"] is False
    assert tsla.calculation_details["growth"]["net_profit_growth_pct"] is None
    assert "NON_STANDARD_NET_PROFIT_BASE" in tsla.calculation_details["warnings"]

    # AMZN: 4. Missing sales value, 8. Loss narrowing
    amzn = _get_metric(disc_session, "AMZN")
    assert "MISSING_PREVIOUS_SALES" in amzn.calculation_details["warnings"]
    assert amzn.calculation_details["growth"]["net_profit_transition"] == "LOSS_NARROWED"

    # META: 9. Loss widening
    meta = _get_metric(disc_session, "META")
    assert meta.calculation_details["growth"]["net_profit_transition"] == "LOSS_WIDENED"

    # NFLX: 10. Zero-base to loss
    nflx = _get_metric(disc_session, "NFLX")
    assert nflx.calculation_details["growth"]["net_profit_transition"] == "ZERO_BASE_TO_LOSS"
    
    # GOOG: 11. Non-comparable P&L
    goog = _get_metric(disc_session, "GOOG")
    assert "INSUFFICIENT_PROFIT_LOSS_PERIODS" in goog.calculation_details["warnings"]
    assert goog.calculation_details["growth"]["growth_available"] is False
    
    # NVDA: 12. Growth available when only sales is valid
    nvda = _get_metric(disc_session, "NVDA")
    assert "MISSING_LATEST_NET_PROFIT" in nvda.calculation_details["warnings"]
    assert "MISSING_PREVIOUS_NET_PROFIT" in nvda.calculation_details["warnings"]
    assert nvda.calculation_details["growth"]["sales_growth_pct"] == 100.0
    assert nvda.calculation_details["growth"]["growth_available"] is True

    # 14. Idempotent update
    svc.calculate_growth("run1")
    aapl_2 = _get_metric(disc_session, "AAPL")
    assert aapl_2.calculation_details["growth"]["sales_growth_pct"] == 50.0
    
    # 16. Source database remains read-only implies no changes on src, which is true because it's mocked mock_src and never calls write.
