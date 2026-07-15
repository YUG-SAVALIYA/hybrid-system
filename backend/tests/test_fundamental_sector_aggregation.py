"""
Tests for FundamentalSectorAggregationService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric, GroupScore
from services.fundamental.fundamental_group_aggregation import FundamentalGroupAggregationService

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()

def _create_company(session, run_id, symbol, sector, 
                    fund_score=None, elig=False, std_debt=True,
                    sg=None, np_trans=None,
                    dte=None, b_trans=None,
                    cc_status=None):
    calc = {
        "financial_strength": {"standard_debt_rule_applicable": std_debt},
        "growth": {},
        "profitability": {},
        "earnings_quality": {"cash_conversion": {}, "profit_stability": {}}
    }
    
    if sg is not None:
        calc["growth"]["sales_growth_pct"] = sg
        calc["growth"]["sales_growth_pct_available"] = True
    if np_trans is not None:
        calc["growth"]["net_profit_transition"] = np_trans
        
    if dte is not None:
        calc["financial_strength"]["debt_to_equity"] = dte
        calc["financial_strength"]["debt_to_equity_available"] = True
    if b_trans is not None:
        calc["financial_strength"]["borrowing_transition"] = b_trans
        
    if cc_status is not None:
        calc["earnings_quality"]["cash_conversion"]["latest_cash_conversion_status"] = cc_status
        
    rec = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=symbol,
        symbol=symbol,
        sector=sector,
        final_fundamental_score=fund_score,
        fundamental_eligible_for_selection=elig,
        calculation_details=calc
    )
    session.add(rec)

def test_fundamental_sector_aggregation_service(disc_session):
    run_id = "run_agg"
    
    # 2. Empty sector
    _create_company(disc_session, run_id, "NONE", None)
    _create_company(disc_session, run_id, "EMPTY", "")
    
    # Odd count median (3), Net profit trans, Borrowing trans, CC trans
    for i in range(3):
        _create_company(disc_session, run_id, f"ODD_{i}", "Sector_Odd", sg=float(i*10), 
                        np_trans="STANDARD_GROWTH", b_trans="DECREASED", cc_status="STRONG_CASH_CONVERSION")
                        
    # Even count median (4)
    for i in range(4):
        _create_company(disc_session, run_id, f"EVE_{i}", "Sector_Even", sg=float(i*10))
        
    # Exclude Missing / non-finite
    _create_company(disc_session, run_id, "M1", "Sector_Miss", sg="inf")
    _create_company(disc_session, run_id, "M2", "Sector_Miss", sg=None)
    _create_company(disc_session, run_id, "M3", "Sector_Miss", sg=10.0)
    
    # Debt metric exclude financial
    _create_company(disc_session, run_id, "FIN1", "Sector_Debt", std_debt=False, dte=5.0)
    _create_company(disc_session, run_id, "STD1", "Sector_Debt", std_debt=True, dte=2.0)
    
    # Sector only financial (N_A)
    _create_company(disc_session, run_id, "FIN2", "Sector_FinOnly", std_debt=False, dte=5.0)
    
    # Eligible counts and warning 
    # Create 5 companies in Sector_OK (min=5)
    for i in range(5):
        _create_company(disc_session, run_id, f"OK_{i}", "Sector_OK", fund_score=75.0, elig=True)
        
    # Existing group score
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="SECTOR",
        entity_name="Sector_Odd",
        technical_score=88.0,
        calculation_details={"technical": {"foo": "bar"}}
    )
    disc_session.add(g)
    
    disc_session.commit()
    
    svc = FundamentalGroupAggregationService(disc_session)
    svc.aggregate_groups(run_id, entity_type="SECTOR")
    
    def get_g(name):
        return disc_session.query(GroupScore).filter_by(entity_name=name).first()

    # 1. Grouped by sector, 2. Empty ignored
    assert get_g(None) is None
    assert get_g("") is None
    assert get_g("Sector_Odd") is not None
    
    # 3. Odd median
    g_odd = get_g("Sector_Odd")
    sg = g_odd.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]
    assert sg["median"] == 10.0
    assert sg["valid_count"] == 3
    
    # 4. Even median
    g_eve = get_g("Sector_Even")
    sg = g_eve.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]
    assert sg["median"] == 15.0 # (10+20)/2
    assert sg["valid_count"] == 4
    
    # 5. Missing / non finite
    g_miss = get_g("Sector_Miss")
    sg = g_miss.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]
    assert sg["median"] == 10.0
    assert sg["valid_count"] == 1
    
    # 6. Metric coverage
    assert sg["coverage_pct"] == (1/3)*100
    
    # 7. Debt exclude financial
    g_debt = get_g("Sector_Debt")
    dte = g_debt.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["debt_to_equity"]
    assert dte["median"] == 2.0
    assert dte["valid_count"] == 1
    assert dte["applicable_count"] == 1
    
    # 8. Sector only financial
    g_fin = get_g("Sector_FinOnly")
    dte = g_fin.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["debt_to_equity"]
    assert dte["valid_count"] == 0
    assert dte["applicable_count"] == 0
    assert dte["reason"] == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    assert dte["coverage_pct"] is None
    
    # 9, 10, 11. Transitions
    agg = g_odd.calculation_details["fundamental"]["raw_aggregation"]
    assert agg["transitions"]["net_profit"]["valid_status_count"] == 3
    assert agg["transitions"]["net_profit"]["counts"]["STANDARD_GROWTH"] == 3
    assert agg["transitions"]["net_profit"]["percentages"]["STANDARD_GROWTH"] == 100.0
    
    assert agg["transitions"]["borrowing"]["counts"]["DECREASED"] == 3
    assert agg["transitions"]["cash_conversion"]["counts"]["STRONG_CASH_CONVERSION"] == 3
    
    # 12. Counts
    g_ok = get_g("Sector_OK")
    agg_ok = g_ok.calculation_details["fundamental"]["raw_aggregation"]
    assert agg_ok["fundamental_score_available_count"] == 5
    assert agg_ok["fundamental_selection_eligible_count"] == 5
    assert "INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS" not in (g_ok.warnings or [])
    
    # 13. Insufficient warning
    assert "INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS" in (g_odd.warnings or [])
    
    # 14, 15. Technical unchanged
    assert g_odd.technical_score == 88.0
    assert g_odd.calculation_details["technical"]["foo"] == "bar"
    
    # 16. Idempotent
    svc.aggregate_groups(run_id, entity_type="SECTOR")
    g_odd2 = get_g("Sector_Odd")
    assert g_odd2.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]["median"] == 10.0
