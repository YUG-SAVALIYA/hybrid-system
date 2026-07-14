"""
Tests for FundamentalIndustryAggregationService.
"""
import uuid
import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric, GroupScore
from services.fundamental.fundamental_industry_aggregation import FundamentalIndustryAggregationService

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

def _create_company(session, run_id, symbol, sector, industry, 
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
        final_fundamental_score=fund_score,
        fundamental_eligible_for_selection=elig,
        calculation_details=calc
    )
    session.add(rec)

def test_fundamental_industry_aggregation_service(disc_session):
    run_id = "run_ind_agg"
    
    # 1. Grouping by sector and industry
    # 2. Same industry under diff sectors
    _create_company(disc_session, run_id, "CO1", "Sector_A", "Ind_Same", fund_score=75.0, sg=10.0)
    _create_company(disc_session, run_id, "CO2", "Sector_A", "Ind_Same", fund_score=75.0, sg=20.0)
    _create_company(disc_session, run_id, "CO3", "Sector_A", "Ind_Same", fund_score=75.0, sg=30.0)
    
    _create_company(disc_session, run_id, "CO4", "Sector_B", "Ind_Same", fund_score=75.0, sg=100.0)
    
    # 3. Odd / Even medians (Ind_Same Sector_A is odd)
    for i in range(4):
        _create_company(disc_session, run_id, f"EVE_{i}", "Sector_C", "Ind_Even", fund_score=75.0, sg=float(i*10))
        
    # 4. Missing / non-finite
    _create_company(disc_session, run_id, "M1", "Sec_Miss", "Ind_Miss", sg="inf")
    _create_company(disc_session, run_id, "M2", "Sec_Miss", "Ind_Miss", sg=None)
    _create_company(disc_session, run_id, "M3", "Sec_Miss", "Ind_Miss", sg=10.0)
    
    # 6. Debt metrics exclude financial
    _create_company(disc_session, run_id, "FIN1", "Sec_Debt", "Ind_Debt", std_debt=False, dte=5.0)
    _create_company(disc_session, run_id, "STD1", "Sec_Debt", "Ind_Debt", std_debt=True, dte=2.0)
    
    # 7. Financial-only
    _create_company(disc_session, run_id, "FIN2", "Sec_FinOnly", "Ind_FinOnly", std_debt=False, dte=5.0)
    
    # 8. All transitions, 9. Counts, 10. Insufficient warning
    _create_company(disc_session, run_id, "T1", "Sec_T", "Ind_T", fund_score=75.0, elig=True, np_trans="STANDARD_GROWTH", b_trans="DECREASED", cc_status="STRONG_CASH_CONVERSION")
    _create_company(disc_session, run_id, "T2", "Sec_T", "Ind_T", fund_score=None, elig=False, np_trans="LOSS_TO_PROFIT", b_trans="ZERO_TO_ZERO", cc_status="WEAK_CASH_CONVERSION")
    
    # 11, 12 Existing data
    g = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="INDUSTRY",
        entity_name="Ind_Same",
        parent_sector="Sector_A",
        parent_industry="",
        horizon="1Y",
        calculation_details={"technical": {"foo": "bar"}, "macro": {"baz": "qux"}}
    )
    disc_session.add(g)
    
    disc_session.commit()
    
    svc = FundamentalIndustryAggregationService(disc_session)
    svc.aggregate_industries(run_id)
    
    def get_g(sector, ind):
        return disc_session.query(GroupScore).filter_by(parent_sector=sector, entity_name=ind).first()

    # 1, 2
    g_a = get_g("Sector_A", "Ind_Same")
    g_b = get_g("Sector_B", "Ind_Same")
    assert g_a is not None
    assert g_b is not None
    
    # 3. Odd/Even
    sg_a = g_a.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]
    assert sg_a["median"] == 20.0
    
    g_eve = get_g("Sector_C", "Ind_Even")
    sg_eve = g_eve.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]
    assert sg_eve["median"] == 15.0 # (10+20)/2
    
    # 4. Exclude missing/non-finite
    g_miss = get_g("Sec_Miss", "Ind_Miss")
    sg_miss = g_miss.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]
    assert sg_miss["median"] == 10.0
    assert sg_miss["valid_count"] == 1
    
    # 5. Coverage
    assert sg_miss["coverage_pct"] == 33.33 # 1 / 3 * 100
    
    # 6. Debt exclude fin
    g_debt = get_g("Sec_Debt", "Ind_Debt")
    dte_debt = g_debt.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["debt_to_equity"]
    assert dte_debt["median"] == 2.0
    assert dte_debt["applicable_count"] == 1
    assert dte_debt["valid_count"] == 1
    
    # 7. Financial only
    g_fin = get_g("Sec_FinOnly", "Ind_FinOnly")
    dte_fin = g_fin.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["debt_to_equity"]
    assert dte_fin["valid_count"] == 0
    assert dte_fin["applicable_count"] == 0
    assert dte_fin["reason"] == "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    assert dte_fin["coverage_pct"] is None
    
    # 8, 9, 10
    g_t = get_g("Sec_T", "Ind_T")
    agg_t = g_t.calculation_details["fundamental"]["raw_aggregation"]
    assert agg_t["transitions"]["net_profit"]["valid_status_count"] == 2
    assert agg_t["transitions"]["borrowing"]["valid_status_count"] == 2
    assert agg_t["transitions"]["cash_conversion"]["valid_status_count"] == 2
    assert agg_t["fundamental_score_available_count"] == 1
    assert agg_t["fundamental_selection_eligible_count"] == 1
    assert "INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS" in g_t.warnings
    
    # 11, 12
    assert g_a.calculation_details["technical"]["foo"] == "bar"
    assert g_a.calculation_details["macro"]["baz"] == "qux"
    
    # 13
    svc.aggregate_industries(run_id)
    g_a2 = get_g("Sector_A", "Ind_Same")
    assert g_a2.calculation_details["fundamental"]["raw_aggregation"]["metrics"]["sales_growth_pct"]["median"] == 20.0
