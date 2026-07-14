"""
Tests for FundamentalBasicIndustryAggregationService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric, GroupScore
from services.fundamental.fundamental_basic_industry_aggregation import FundamentalBasicIndustryAggregationService

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

def _create_company(session, run_id, symbol, sector, industry, basic_industry, 
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
        industry=industry,
        basic_industry=basic_industry,
        final_fundamental_score=fund_score,
        fundamental_eligible_for_selection=elig,
        calculation_details=calc
    )
    session.add(rec)

def test_fundamental_basic_industry_aggregation_service(disc_session):
    run_id = "run_bi_agg"
    
    # 1. Grouping by sec/ind/bi
    # 2. Same bi diff branch
    _create_company(disc_session, run_id, "CO1", "SecA", "IndA", "BI_Same", fund_score=75.0, sg=10.0)
    _create_company(disc_session, run_id, "CO2", "SecA", "IndA", "BI_Same", fund_score=75.0, sg=20.0)
    _create_company(disc_session, run_id, "CO3", "SecA", "IndA", "BI_Same", fund_score=75.0, sg=30.0) # Odd median -> 20
    
    _create_company(disc_session, run_id, "CO4", "SecB", "IndB", "BI_Same", fund_score=75.0, sg=100.0)
    
    # 3. Empty hierarchy
    _create_company(disc_session, run_id, "CO5", "SecA", "IndA", None, sg=100.0)
    
    # 4. Odd (above), 5. Even
    _create_company(disc_session, run_id, "EVE1", "SecE", "IndE", "BI_Even", fund_score=75.0, sg=10.0)
    _create_company(disc_session, run_id, "EVE2", "SecE", "IndE", "BI_Even", fund_score=75.0, sg=20.0) # Even median -> 15
    
    # 6. Missing/non-finite
    _create_company(disc_session, run_id, "M1", "SecM", "IndM", "BI_Miss", sg="inf")
    _create_company(disc_session, run_id, "M2", "SecM", "IndM", "BI_Miss", sg=None)
    _create_company(disc_session, run_id, "M3", "SecM", "IndM", "BI_Miss", sg=10.0)
    
    # 8. Debt exclude fin
    _create_company(disc_session, run_id, "D1", "SecD", "IndD", "BI_Debt", std_debt=False, dte=5.0)
    _create_company(disc_session, run_id, "D2", "SecD", "IndD", "BI_Debt", std_debt=True, dte=2.0)
    
    # 9. Financial only
    _create_company(disc_session, run_id, "F1", "SecF", "IndF", "BI_Fin", std_debt=False, dte=5.0)
    
    # 10, 11, 12. Transitions, 13. Counts, 14. Insufficient (only 1 available score)
    _create_company(disc_session, run_id, "T1", "SecT", "IndT", "BI_Trans", fund_score=75.0, elig=True, np_trans="STANDARD_GROWTH", b_trans="DECREASED", cc_status="STRONG_CASH_CONVERSION")
    _create_company(disc_session, run_id, "T2", "SecT", "IndT", "BI_Trans", fund_score=None, elig=False, np_trans="LOSS_TO_PROFIT", b_trans="ZERO_TO_ZERO", cc_status="WEAK_CASH_CONVERSION")
    
    # 15, 16. Existing fields
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name="BI_Same",
        parent_sector="SecA",
        parent_industry="IndA",
        horizon="1Y",
        calculation_details={"technical": {"foo": "bar"}, "macro": {"baz": "qux"}, "fundamental": {"other": "value"}}
    )
    disc_session.add(g)
    
    disc_session.commit()
    
    svc = FundamentalBasicIndustryAggregationService(disc_session)
    svc.aggregate_basic_industries(run_id)
    
    def get_g(sec, ind, bi):
        return disc_session.query(GroupScore).filter_by(parent_sector=sec, parent_industry=ind, entity_name=bi).first()
        
    # 1, 2
    g_a = get_g("SecA", "IndA", "BI_Same")
    g_b = get_g("SecB", "IndB", "BI_Same")
    assert g_a is not None
    assert g_b is not None
    
    # 3. Empty skipped
    assert get_g("SecA", "IndA", None) is None
    
    # 4. Odd
    assert g_a.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]["median"] == 20.0
    
    # 5. Even
    g_eve = get_g("SecE", "IndE", "BI_Even")
    assert g_eve.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]["median"] == 15.0
    
    # 6. Missing/non-finite, 7. Coverage
    g_miss = get_g("SecM", "IndM", "BI_Miss")
    sg_miss = g_miss.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]
    assert sg_miss["median"] == 10.0
    assert sg_miss["valid_count"] == 1
    assert sg_miss["applicable_count"] == 3
    assert sg_miss["coverage_pct"] == 33.33
    
    # 8
    g_debt = get_g("SecD", "IndD", "BI_Debt")
    dte_debt = g_debt.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["debt_to_equity"]
    assert dte_debt["median"] == 2.0
    assert dte_debt["valid_count"] == 1
    assert dte_debt["applicable_count"] == 1
    
    # 9
    g_fin = get_g("SecF", "IndF", "BI_Fin")
    dte_fin = g_fin.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["debt_to_equity"]
    assert dte_fin["median"] is None
    assert dte_fin["applicable_count"] == 0
    assert dte_fin["reason"] == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    
    # 10, 11, 12, 13, 14
    g_t = get_g("SecT", "IndT", "BI_Trans")
    agg_t = g_t.calculation_details["fundamental"]["raw_aggregation"]
    assert agg_t["transitions"]["net_profit"]["valid_status_count"] == 2
    assert agg_t["fundamental_score_available_count"] == 1
    assert "INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS" in g_t.warnings
    
    # 15, 16
    assert g_a.calculation_details["technical"]["foo"] == "bar"
    assert g_a.calculation_details["macro"]["baz"] == "qux"
    assert g_a.calculation_details["fundamental"]["other"] == "value"
    
    # 17
    svc.aggregate_basic_industries(run_id)
    g_a2 = get_g("SecA", "IndA", "BI_Same")
    assert g_a2.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]["median"] == 20.0
