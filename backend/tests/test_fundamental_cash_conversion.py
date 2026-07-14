"""
Tests for FundamentalCashConversionService.
"""
import uuid
import pytest
from unittest.mock import MagicMock
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_cash_conversion import FundamentalCashConversionService

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


def test_fundamental_cash_conversion_logic(disc_session):
    selections = [
        {"source_company_id": "c1", "symbol": "C1", "overview_id": "o1", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c2", "symbol": "C2", "overview_id": "o2", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c3", "symbol": "C3", "overview_id": "o3", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c4", "symbol": "C4", "overview_id": "o4", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c5", "symbol": "C5", "overview_id": "o5", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c6", "symbol": "C6", "overview_id": "o6", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c7", "symbol": "C7", "overview_id": "o7", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c8", "symbol": "C8", "overview_id": "o8", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c9", "symbol": "C9", "overview_id": "o9", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c10", "symbol": "C10", "overview_id": "o10", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c11", "symbol": "C11", "overview_id": "o11", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c12", "symbol": "C12", "overview_id": "o12", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c13", "symbol": "C13", "overview_id": "o13", "profit_loss_cash_flow_common": {"comparable": True, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c14", "symbol": "C14", "overview_id": "o14", "profit_loss_cash_flow_common": {"comparable": False, "latest_period": "L", "previous_period": "P"}, "warnings": []},
        {"source_company_id": "c15", "symbol": "C15", "overview_id": "o15", "profit_loss_cash_flow_common": {"comparable": False, "latest_period": "L", "previous_period": "P"}, "warnings": ["NON_CONSECUTIVE_ANNUAL_PERIODS"]},
    ]

    pl_records = [
        MockRow(company_id="o1", period="P", net_profit=100.0), MockRow(company_id="o1", period="L", net_profit=100.0),
        MockRow(company_id="o2", period="P", net_profit=100.0), MockRow(company_id="o2", period="L", net_profit=100.0),
        MockRow(company_id="o3", period="P", net_profit=100.0), MockRow(company_id="o3", period="L", net_profit=100.0),
        MockRow(company_id="o4", period="P", net_profit=100.0), MockRow(company_id="o4", period="L", net_profit=100.0),
        MockRow(company_id="o5", period="P", net_profit=100.0), MockRow(company_id="o5", period="L", net_profit=100.0),
        MockRow(company_id="o6", period="P", net_profit=100.0), MockRow(company_id="o6", period="L", net_profit=-100.0),
        MockRow(company_id="o7", period="P", net_profit=100.0), MockRow(company_id="o7", period="L", net_profit=-100.0),
        MockRow(company_id="o8", period="P", net_profit=100.0), MockRow(company_id="o8", period="L", net_profit=-100.0),
        MockRow(company_id="o9", period="P", net_profit=100.0), MockRow(company_id="o9", period="L", net_profit=0.0),
        MockRow(company_id="o10", period="P", net_profit=100.0), MockRow(company_id="o10", period="L", net_profit=0.0),
        MockRow(company_id="o11", period="P", net_profit=100.0), MockRow(company_id="o11", period="L", net_profit=0.0),
        MockRow(company_id="o12", period="P", net_profit=100.0), MockRow(company_id="o12", period="L", net_profit=100.0),
        MockRow(company_id="o13", period="P", net_profit=100.0), MockRow(company_id="o13", period="L", net_profit=100.0),
    ]

    cf_records = [
        # C1: 1. Ratio > 1 (1.2), 12. Deteriorated (1.4 -> 1.2, change -0.2)
        MockRow(company_id="o1", period="P", cash_from_operating_activity=140.0), 
        MockRow(company_id="o1", period="L", cash_from_operating_activity=120.0),
        
        # C2: 2. 0.5-1 (0.8), 11. Stable (0.8 -> 0.8)
        MockRow(company_id="o2", period="P", cash_from_operating_activity=80.0), 
        MockRow(company_id="o2", period="L", cash_from_operating_activity=80.0),
        
        # C3: 3. 0-0.5 (0.2), 10. Improved (0.0 -> 0.2)
        MockRow(company_id="o3", period="P", cash_from_operating_activity=0.0), 
        MockRow(company_id="o3", period="L", cash_from_operating_activity=20.0),
        
        # C4: 4. Pos PAT, Neg OCF
        MockRow(company_id="o4", period="P", cash_from_operating_activity=0.0), 
        MockRow(company_id="o4", period="L", cash_from_operating_activity=-10.0),
        
        # C5: 5. Pos PAT, Zero OCF
        MockRow(company_id="o5", period="P", cash_from_operating_activity=10.0), 
        MockRow(company_id="o5", period="L", cash_from_operating_activity=0.0),
        
        # C6: 6. Loss with Pos OCF
        MockRow(company_id="o6", period="P", cash_from_operating_activity=10.0), 
        MockRow(company_id="o6", period="L", cash_from_operating_activity=10.0),
        
        # C7: 7. Loss with Neg OCF
        MockRow(company_id="o7", period="P", cash_from_operating_activity=10.0), 
        MockRow(company_id="o7", period="L", cash_from_operating_activity=-10.0),
        
        # C8: 8. Loss with Zero OCF
        MockRow(company_id="o8", period="P", cash_from_operating_activity=10.0), 
        MockRow(company_id="o8", period="L", cash_from_operating_activity=0.0),
        
        # C9: 9. Zero PAT, Pos OCF
        MockRow(company_id="o9", period="P", cash_from_operating_activity=10.0), 
        MockRow(company_id="o9", period="L", cash_from_operating_activity=10.0),
        
        # C10: 9. Zero PAT, Neg OCF
        MockRow(company_id="o10", period="P", cash_from_operating_activity=10.0), 
        MockRow(company_id="o10", period="L", cash_from_operating_activity=-10.0),
        
        # C11: 9. Zero PAT, Zero OCF
        MockRow(company_id="o11", period="P", cash_from_operating_activity=10.0), 
        MockRow(company_id="o11", period="L", cash_from_operating_activity=0.0),
        
        # C12: 11. Stable at bounds (+0.10, -0.10)
        MockRow(company_id="o12", period="P", cash_from_operating_activity=100.0), 
        MockRow(company_id="o12", period="L", cash_from_operating_activity=110.0),
        
        # C13: 13. Missing latest OCF
        MockRow(company_id="o13", period="P", cash_from_operating_activity=10.0), 
        MockRow(company_id="o13", period="L", cash_from_operating_activity=None),
    ]

    h_records = [
        MockRow(id="c1", share_symbol="C1", sectore="Tech", industry="Hard", categorized_industry="Phones"),
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

    svc = FundamentalCashConversionService(mock_src, disc_session)
    svc._period_svc = MockPeriodSvc(selections)
    
    # 16, 17. Deep merge and unchanged values
    existing = CompanyFundamentalMetric(
        id=str(uuid.uuid4()), run_id="run1", source_company_id="c1", symbol="C1",
        growth_score=99.0, 
        calculation_details={
            "growth": {"x": 1}, 
            "earnings_quality": {"profit_stability": {"stable": True}},
            "warnings": ["EXISTING_WARNING"]
        }
    )
    disc_session.add(existing)
    disc_session.commit()

    svc.calculate_cash_conversion("run1")

    # C1
    c1 = _get_metric(disc_session, "C1")
    assert c1.growth_score == 99.0 # 16. Preserved
    assert c1.calculation_details["growth"]["x"] == 1 # 16. Preserved
    assert c1.calculation_details["earnings_quality"]["profit_stability"]["stable"] is True # 17. Deep merged
    cc = c1.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["ocf_to_pat"] == 1.2 # 1. Ratio > 1
    assert cc["latest"]["status"] == "STRONG_CASH_CONVERSION"
    assert cc["ocf_to_pat_change"] == pytest.approx(-0.2)
    assert cc["trend_status"] == "DETERIORATED" # 12. Deteriorated
    assert cc["cash_conversion_available"] is True

    # C2
    c2 = _get_metric(disc_session, "C2")
    cc = c2.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["ocf_to_pat"] == 0.8
    assert cc["latest"]["status"] == "ADEQUATE_CASH_CONVERSION" # 2. 0.5-1.0
    assert cc["trend_status"] == "STABLE" # 11. Stable
    
    # C3
    c3 = _get_metric(disc_session, "C3")
    cc = c3.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["ocf_to_pat"] == 0.2
    assert cc["latest"]["status"] == "WEAK_CASH_CONVERSION" # 3. 0-0.5
    assert cc["trend_status"] == "IMPROVED" # 10. Improved
    
    # C4
    c4 = _get_metric(disc_session, "C4")
    cc = c4.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["status"] == "NEGATIVE_OPERATING_CASH_FLOW" # 4. Pos PAT, Neg OCF
    
    # C5
    c5 = _get_metric(disc_session, "C5")
    cc = c5.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["status"] == "PROFIT_WITH_ZERO_OPERATING_CASH_FLOW" # 5. Pos PAT, Zero OCF
    
    # C6
    c6 = _get_metric(disc_session, "C6")
    cc = c6.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["status"] == "LOSS_WITH_POSITIVE_OPERATING_CASH_FLOW" # 6. Loss, Pos OCF
    assert "NON_POSITIVE_LATEST_PAT_BASE" in c6.calculation_details["warnings"]
    assert cc["latest"]["ocf_to_pat"] is None
    assert cc["cash_conversion_available"] is True
    
    # C7
    c7 = _get_metric(disc_session, "C7")
    cc = c7.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["status"] == "LOSS_WITH_NEGATIVE_OPERATING_CASH_FLOW" # 7. Loss, Neg OCF
    
    # C8
    c8 = _get_metric(disc_session, "C8")
    cc = c8.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["status"] == "LOSS_WITH_ZERO_OPERATING_CASH_FLOW" # 8. Loss, Zero OCF
    
    # C9
    c9 = _get_metric(disc_session, "C9")
    cc = c9.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["status"] == "ZERO_PAT_WITH_POSITIVE_OPERATING_CASH_FLOW" # 9. Zero PAT, Pos OCF
    
    # C10
    c10 = _get_metric(disc_session, "C10")
    cc = c10.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["status"] == "ZERO_PAT_WITH_NEGATIVE_OPERATING_CASH_FLOW" # 9. Zero PAT, Neg OCF
    
    # C11
    c11 = _get_metric(disc_session, "C11")
    cc = c11.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["latest"]["status"] == "ZERO_PAT_WITH_ZERO_OPERATING_CASH_FLOW" # 9. Zero PAT, Zero OCF
    
    # C12
    c12 = _get_metric(disc_session, "C12")
    cc = c12.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["ocf_to_pat_change"] == pytest.approx(0.10)
    assert cc["trend_status"] == "STABLE" # 11. Stable boundary
    
    # C13
    c13 = _get_metric(disc_session, "C13")
    assert "MISSING_LATEST_OPERATING_CASH_FLOW" in c13.calculation_details["warnings"] # 13. Missing latest OCF
    cc = c13.calculation_details["earnings_quality"]["cash_conversion"]
    assert cc["cash_conversion_available"] is False
    
    # C14
    c14 = _get_metric(disc_session, "C14")
    assert "NO_COMMON_PL_CF_PERIOD" in c14.calculation_details["warnings"] # 14. No common
    
    # C15
    c15 = _get_metric(disc_session, "C15")
    assert "NON_CONSECUTIVE_ANNUAL_PERIODS" in c15.calculation_details["warnings"] # 15. Non consecutive
    assert "NO_COMMON_PL_CF_PERIOD" not in c15.calculation_details["warnings"]

    # 18. Idempotent update
    svc.calculate_cash_conversion("run1")
    c1_2 = _get_metric(disc_session, "C1")
    assert c1_2.calculation_details["earnings_quality"]["cash_conversion"]["latest"]["status"] == "STRONG_CASH_CONVERSION"

