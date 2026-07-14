"""
Tests for FundamentalProfitStabilityService.
"""
import uuid
import pytest
from unittest.mock import MagicMock
from sqlalchemy import text
import math

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_profit_stability import FundamentalProfitStabilityService

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

class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_fundamental_profit_stability_logic(disc_session):
    h_records = [
        MockRow(source_company_id="c1", symbol="C1", overview_id="o1", sector="Tech", industry="Hard", basic_industry="Phones"),
        MockRow(source_company_id="c2", symbol="C2", overview_id="o2", sector="Tech", industry="Hard", basic_industry="Phones"),
        MockRow(source_company_id="c3", symbol="C3", overview_id="o3", sector="Tech", industry="Hard", basic_industry="Phones"),
        MockRow(source_company_id="c4", symbol="C4", overview_id="o4", sector="Tech", industry="Hard", basic_industry="Phones"),
        MockRow(source_company_id="c5", symbol="C5", overview_id="o5", sector="Tech", industry="Hard", basic_industry="Phones"),
        MockRow(source_company_id="c6", symbol="C6", overview_id="o6", sector="Tech", industry="Hard", basic_industry="Phones"),
    ]

    pl_records = [
        # C1: Exactly 3 periods (1.), 10. Valid pos growth, 14. Volatility
        MockRow(company_id="o1", period="Mar 2023", net_profit=100.0),
        MockRow(company_id="o1", period="Mar 2024", net_profit=120.0), # 20%
        MockRow(company_id="o1", period="Mar 2025", net_profit=150.0), # 25%
        
        # C2: 2. Max 5, 3. older beyond 5 ignored. 
        MockRow(company_id="o2", period="Mar 2020", net_profit=50.0),
        MockRow(company_id="o2", period="Mar 2021", net_profit=60.0),
        MockRow(company_id="o2", period="Mar 2022", net_profit=70.0),
        MockRow(company_id="o2", period="Mar 2023", net_profit=80.0),
        MockRow(company_id="o2", period="Mar 2024", net_profit=90.0),
        MockRow(company_id="o2", period="Mar 2025", net_profit=100.0),
        
        # C3: 4. Multi-year gap stops series, 15. One growth obs leaves vol unavailable
        MockRow(company_id="o3", period="Mar 2022", net_profit=50.0),
        MockRow(company_id="o3", period="Mar 2024", net_profit=100.0), # gap
        MockRow(company_id="o3", period="Mar 2025", net_profit=120.0), # only 2 periods taken
        
        # C4: 5. Missing PAT stops series, 6. Older not skipped, 11. Profit to loss, 12. zero/neg older PAT excludes
        MockRow(company_id="o4", period="Mar 2022", net_profit=100.0),
        MockRow(company_id="o4", period="Mar 2023", net_profit=None),
        MockRow(company_id="o4", period="Mar 2024", net_profit=50.0),
        MockRow(company_id="o4", period="Mar 2025", net_profit=-50.0), # 11. 50 -> -50 = -200%
        
        # C5: 7. Pos/Loss/Zero ratios, 8. Latest pos streak, 9. sign change count, 16. MIXED_PROFITABILITY
        MockRow(company_id="o5", period="Mar 2021", net_profit=100.0),
        MockRow(company_id="o5", period="Mar 2022", net_profit=0.0), # + to 0
        MockRow(company_id="o5", period="Mar 2023", net_profit=-50.0), # 0 to -
        MockRow(company_id="o5", period="Mar 2024", net_profit=10.0), # - to +
        MockRow(company_id="o5", period="Mar 2025", net_profit=20.0),
        
        # C6: 16. CONSISTENTLY_NON_PROFITABLE
        MockRow(company_id="o6", period="Mar 2023", net_profit=-10.0),
        MockRow(company_id="o6", period="Mar 2024", net_profit=-10.0),
        MockRow(company_id="o6", period="Mar 2025", net_profit=-10.0),
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

    svc = FundamentalProfitStabilityService(mock_src, disc_session)
    
    # 17. Cash conversion JSON unchanged, 18. Other fields unchanged
    existing = CompanyFundamentalMetric(
        id=str(uuid.uuid4()), run_id="run1", source_company_id="c1", symbol="C1",
        growth_score=99.0, 
        calculation_details={
            "growth": {"x": 1}, 
            "earnings_quality": {"cash_conversion": {"ocf_to_pat": 1.5}},
            "warnings": ["EXISTING_WARNING"]
        }
    )
    disc_session.add(existing)
    disc_session.commit()

    svc.calculate_profit_stability("run1")

    # C1
    c1 = _get_metric(disc_session, "C1")
    assert c1.growth_score == 99.0
    assert c1.calculation_details["growth"]["x"] == 1
    assert c1.calculation_details["earnings_quality"]["cash_conversion"]["ocf_to_pat"] == 1.5 # 17, 18. Unchanged
    ps = c1.calculation_details["earnings_quality"]["profit_stability"]
    assert ps["selected_period_count"] == 3 # 1. Exactly 3
    assert ps["profit_stability_available"] is True
    assert ps["status"] == "CONSISTENTLY_PROFITABLE" # 16.
    assert ps["mean_pat_growth_pct"] == 22.5 # (20 + 25)/2 = 22.5. 10. Valid growth, 13. Mean PAT growth
    assert ps["pat_growth_volatility_available"] is True
    assert ps["pat_growth_volatility_pct"] == pytest.approx(2.5) # sqrt(((20-22.5)^2 + (25-22.5)^2)/2) = 2.5
    
    # C2
    c2 = _get_metric(disc_session, "C2")
    ps = c2.calculation_details["earnings_quality"]["profit_stability"]
    assert ps["selected_period_count"] == 5 # 2. Max 5
    assert not any("2020" in p["period"] for p in ps["periods"]) # 3. older ignored
    
    # C3
    c3 = _get_metric(disc_session, "C3")
    ps = c3.calculation_details["earnings_quality"]["profit_stability"]
    assert ps["selected_period_count"] == 2 # 4. gap stops series
    assert ps["profit_stability_available"] is False
    assert ps["pat_growth_volatility_available"] is False # 15. one obs
    assert "INSUFFICIENT_CONSECUTIVE_PAT_PERIODS" in c3.calculation_details["warnings"]
    assert "NON_CONSECUTIVE_ANNUAL_PERIODS" in c3.calculation_details["warnings"]
    
    # C4
    c4 = _get_metric(disc_session, "C4")
    ps = c4.calculation_details["earnings_quality"]["profit_stability"]
    assert ps["selected_period_count"] == 2 # 5. missing stops series, 6. older not skipped
    assert ps["valid_pat_growth_observation_count"] == 1
    assert "MISSING_NET_PROFIT_IN_STABILITY_SERIES" in c4.calculation_details["warnings"]
    # 2024 to 2025 growth (50 to -50)
    assert ps["mean_pat_growth_pct"] == -200.0 # 11. growth below -100
    
    # C5
    c5 = _get_metric(disc_session, "C5")
    ps = c5.calculation_details["earnings_quality"]["profit_stability"]
    assert ps["selected_period_count"] == 5
    assert ps["positive_pat_period_ratio"] == 60.0 # 3 / 5
    assert ps["loss_pat_period_ratio"] == 20.0 # 1 / 5
    assert ps["zero_pat_period_ratio"] == 20.0 # 1 / 5
    assert ps["latest_positive_pat_streak"] == 2 # 8. 2025, 2024
    assert ps["pat_sign_change_count"] == 3 # 9. + to 0, 0 to -, - to +
    assert ps["status"] == "MOSTLY_PROFITABLE" # Wait, 60 is MOSTLY, not MIXED. Let's check: >= 60 is MOSTLY. Correct.
    # Growth obs:
    # 21 to 22: 100 to 0 -> -100%
    # 22 to 23: 0 to -50 -> excluded (12.)
    # 23 to 24: -50 to 10 -> excluded
    # 24 to 25: 10 to 20 -> 100%
    assert ps["valid_pat_growth_observation_count"] == 2
    assert ps["mean_pat_growth_pct"] == 0.0 # (-100 + 100) / 2
    
    # C6
    c6 = _get_metric(disc_session, "C6")
    ps = c6.calculation_details["earnings_quality"]["profit_stability"]
    assert ps["status"] == "CONSISTENTLY_NON_PROFITABLE" # 16.
    assert ps["valid_pat_growth_observation_count"] == 0
    
    # 19. Idempotent
    svc.calculate_profit_stability("run1")
    c1_2 = _get_metric(disc_session, "C1")
    assert c1_2.calculation_details["earnings_quality"]["profit_stability"]["status"] == "CONSISTENTLY_PROFITABLE"
